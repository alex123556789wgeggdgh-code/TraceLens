/*
 * TraceLens 更新引擎自检（UPDATE-CHANNEL-1）
 * ------------------------------------------
 * 纯 Node、零依赖、零网络。跑 `node desktop/lib/selftest.mjs`，exit 0 = 全过。
 *
 * 覆盖的是"会不会把用户搞坏"的那些判据，不是覆盖率数字：
 *   版本比较（0.10 > 0.9 这类真会出事的地方）、路径穿越、条目多一个少一个、
 *   包被改一个字节、降级、能力门禁、原子激活、启动失败自动回退。
 *
 * 夹具全部现场造：自带一个 store 模式的 mini zip writer（40 行），
 * 这样"篡改包"这类用例不依赖任何外部工具，也不会被 `make_update_package.py`
 * 的改动牵着走（那是另一个被测对象）。
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const version = require('./version.js');
const zipLib = require('./zip.js');
const mf = require('./manifest.js');
const { createUpdater } = require('../updater.js');

let passed = 0;
const failures = [];

function check(title, fn) {
  try {
    fn();
    passed += 1;
    console.log(`  ok   ${title}`);
  } catch (err) {
    failures.push({ title, message: err && err.message ? err.message : String(err) });
    console.log(`  FAIL ${title}\n         ${err && err.message ? err.message : err}`);
  }
}

async function checkAsync(title, fn) {
  try {
    await fn();
    passed += 1;
    console.log(`  ok   ${title}`);
  } catch (err) {
    failures.push({ title, message: err && err.message ? err.message : String(err) });
    console.log(`  FAIL ${title}\n         ${err && err.message ? err.message : err}`);
  }
}

function eq(actual, expected, label) {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) {
    throw new Error(`${label || '不相等'}：期望 ${b}，实际 ${a}`);
  }
}

function ok(value, label) {
  if (!value) {
    throw new Error(label || '断言失败');
  }
}

function sha256(buf) {
  return crypto.createHash('sha256').update(buf).digest('hex');
}

// --------------------------------------------------------------------------- #
// mini zip writer（store 模式）：只用于造夹具
// --------------------------------------------------------------------------- #
function writeZip(entries) {
  const parts = [];
  const central = [];
  let offset = 0;
  for (const entry of entries) {
    const nameBuf = Buffer.from(entry.name, 'utf8');
    const data = Buffer.isBuffer(entry.data) ? entry.data : Buffer.from(entry.data);
    const loc = Buffer.alloc(30);
    loc.writeUInt32LE(0x04034b50, 0);
    loc.writeUInt16LE(20, 4);
    loc.writeUInt16LE(0x800, 6);
    loc.writeUInt16LE(0, 8);
    loc.writeUInt32LE(0, 14);
    loc.writeUInt32LE(data.length, 18);
    loc.writeUInt32LE(data.length, 22);
    loc.writeUInt16LE(nameBuf.length, 26);
    parts.push(loc, nameBuf, data);

    const cen = Buffer.alloc(46);
    cen.writeUInt32LE(0x02014b50, 0);
    cen.writeUInt16LE(20, 4);
    cen.writeUInt16LE(20, 6);
    cen.writeUInt16LE(0x800, 8);
    cen.writeUInt32LE(0, 16);
    cen.writeUInt32LE(data.length, 20);
    cen.writeUInt32LE(data.length, 24);
    cen.writeUInt16LE(nameBuf.length, 28);
    cen.writeUInt32LE(offset, 42);
    central.push(cen, nameBuf);
    offset += 30 + nameBuf.length + data.length;
  }
  const cd = Buffer.concat(central);
  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(0x06054b50, 0);
  eocd.writeUInt16LE(entries.length, 8);
  eocd.writeUInt16LE(entries.length, 10);
  eocd.writeUInt32LE(cd.length, 12);
  eocd.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, cd, eocd]);
}

/** 造一个合法包；`mutate` 可改 manifest / 条目，用来造坏包。 */
function makePackage(versionStr, { files, mutateManifest, mutateEntries, capabilities = ['web'] } = {}) {
  const content = files || { 'web/index.html': `<h1>${versionStr}</h1>`, 'web/app.js': `// ${versionStr}\n` };
  const entries = Object.keys(content).map((name) => ({ name, data: Buffer.from(content[name]) }));
  const manifest = {
    schema: 'tracelens.update-package/1',
    version: versionStr,
    channel: 'stable',
    min_host: null,
    capabilities,
    created_at: '1970-01-01T00:00:00Z',
    notes: `fixture ${versionStr}`,
    files: entries.map((e) => ({ path: e.name, size: e.data.length, sha256: sha256(e.data) })),
  };
  if (mutateManifest) {
    mutateManifest(manifest);
  }
  const withManifest = [{ name: 'manifest.json', data: Buffer.from(JSON.stringify(manifest, null, 2)) }]
    .concat(mutateEntries ? mutateEntries(entries) : entries);
  return { buffer: writeZip(withManifest), manifest };
}

function makeSource(dir, releases) {
  fs.mkdirSync(dir, { recursive: true });
  const rows = [];
  for (const r of releases) {
    fs.writeFileSync(path.join(dir, r.file), r.buffer);
    rows.push({
      version: r.version,
      file: r.file,
      size: r.buffer.length,
      sha256: sha256(r.buffer),
      capabilities: r.capabilities || ['web'],
      min_host: r.min_host || null,
      notes: r.notes || '',
      published_at: '1970-01-01T00:00:00Z',
    });
  }
  const index = { schema: 'tracelens.update-index/1', channel: 'stable', latest: rows[rows.length - 1].version, releases: rows };
  fs.writeFileSync(path.join(dir, 'index.json'), JSON.stringify(index, null, 2));
  return dir;
}

function freshUserData(tag) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), `tl-upd-${tag}-`));
  return dir;
}

// --------------------------------------------------------------------------- #
console.log('1. 版本比较');
check('0.10.0 > 0.9.0（字符串比较会错的那一例）', () => eq(version.compare('0.10.0', '0.9.0'), 1));
check('1.0.0 > 1.0.0-rc.1', () => eq(version.compare('1.0.0', '1.0.0-rc.1'), 1));
check('v1.2.3 == 1.2.3', () => eq(version.compare('v1.2.3', '1.2.3'), 0));
check('1.2 == 1.2.0', () => eq(version.compare('1.2', '1.2.0'), 0));
check('1.0.0-alpha < 1.0.0-alpha.1', () => eq(version.compare('1.0.0-alpha', '1.0.0-alpha.1'), -1));
check('非法版本号不猜：compare 抛错', () => {
  let threw = false;
  try { version.compare('abc', '1.0.0'); } catch (err) { threw = true; }
  ok(threw, '应当抛 TypeError');
});
check('isValid 拒绝 "1.x.0"', () => ok(!version.isValid('1.x.0')));

console.log('2. 路径安全');
for (const bad of ['../x', 'a/../../b', '/etc/passwd', 'C:/x', 'web\\app.js', 'web//a.js',
  'web/./a.js', 'web/a.js ', '', 'web/\u0000x']) {
  check(`拒绝 ${JSON.stringify(bad)}`, () => ok(!mf.isSafeEntryPath(bad)));
}
for (const good of ['web/app.js', 'runtime/agent_explainer/server.py', 'web/i18n/zh.json']) {
  check(`允许 ${good}`, () => ok(mf.isSafeEntryPath(good)));
}

console.log('3. zip 读取');
{
  const pkg = makePackage('1.0.0');
  const tmp = path.join(freshUserData('zip'), 'x.tlpkg');
  fs.writeFileSync(tmp, pkg.buffer);
  const handle = zipLib.open(tmp);
  check('条目数 = 2 个文件 + manifest', () => eq(handle.names.length, 3));
  check('能读回 web/app.js 原文', () => eq(handle.read('web/app.js').toString(), '// 1.0.0\n'));
  check('读不存在的条目要报错', () => {
    let reason = null;
    try { handle.read('web/nope.js'); } catch (err) { reason = err.reason; }
    eq(reason, 'not_found');
  });
  check('非 zip 文件给出 not_a_zip', () => {
    const bad = path.join(path.dirname(tmp), 'bad.tlpkg');
    fs.writeFileSync(bad, 'not a zip at all, just text');
    let reason = null;
    try { zipLib.open(bad); } catch (err) { reason = err.reason; }
    eq(reason, 'not_a_zip');
  });
}

console.log('4. manifest 校验');
{
  const good = makePackage('1.0.0').manifest;
  check('合法 manifest 通过并被规范化', () => eq(mf.validateManifest(good).version, '1.0.0'));
  check('schema 错 → bad_schema', () => {
    let r = null;
    try { mf.validateManifest(Object.assign({}, good, { schema: 'x' })); } catch (e) { r = e.reason; }
    eq(r, 'bad_schema');
  });
  check('版本非法 → bad_version', () => {
    let r = null;
    try { mf.validateManifest(Object.assign({}, good, { version: 'latest' })); } catch (e) { r = e.reason; }
    eq(r, 'bad_version');
  });
  check('缺 web 能力 → missing_web', () => {
    let r = null;
    try { mf.validateManifest(Object.assign({}, good, { capabilities: ['runtime'] })); } catch (e) { r = e.reason; }
    eq(r, 'missing_web');
  });
  check('files 里带 ../ → unsafe_path', () => {
    let r = null;
    const m = JSON.parse(JSON.stringify(good));
    m.files[0].path = '../evil.js';
    try { mf.validateManifest(m); } catch (e) { r = e.reason; }
    eq(r, 'unsafe_path');
  });
  check('files 落在未知顶层目录 → bad_top_level', () => {
    let r = null;
    const m = JSON.parse(JSON.stringify(good));
    m.files[0].path = 'shell/main.js';
    try { mf.validateManifest(m); } catch (e) { r = e.reason; }
    eq(r, 'bad_top_level');
  });
  check('重复路径 → duplicate_path', () => {
    let r = null;
    const m = JSON.parse(JSON.stringify(good));
    m.files.push(Object.assign({}, m.files[0]));
    try { mf.validateManifest(m); } catch (e) { r = e.reason; }
    eq(r, 'duplicate_path');
  });
}

console.log('5. 包内对账');
{
  const tmp = path.join(freshUserData('verify'), 'x.tlpkg');
  const pkg = makePackage('1.0.0');
  fs.writeFileSync(tmp, pkg.buffer);
  const handle = zipLib.open(tmp);
  check('逐文件哈希全过', () => eq(mf.verifyPackage(handle, mf.validateManifest(pkg.manifest)).checked, 2));
  check('包里多一个未申报文件 → undeclared_entries', () => {
    const bad = makePackage('1.0.0', {
      mutateEntries: (entries) => entries.concat([{ name: 'web/evil.js', data: Buffer.from('x') }]),
    });
    const p = path.join(os.tmpdir(), `tl-extra-${Date.now()}.tlpkg`);
    fs.writeFileSync(p, bad.buffer);
    let r = null;
    try { mf.verifyPackage(zipLib.open(p), mf.validateManifest(bad.manifest)); } catch (e) { r = e.reason; }
    eq(r, 'undeclared_entries');
  });
  check('包里少一个文件 → missing_entries', () => {
    const bad = makePackage('1.0.0', { mutateEntries: (entries) => entries.slice(0, 1) });
    const p = path.join(os.tmpdir(), `tl-miss-${Date.now()}.tlpkg`);
    fs.writeFileSync(p, bad.buffer);
    let r = null;
    try { mf.verifyPackage(zipLib.open(p), mf.validateManifest(bad.manifest)); } catch (e) { r = e.reason; }
    eq(r, 'missing_entries');
  });
  check('文件内容被改一个字节 → hash_mismatch', () => {
    // 必须**等长**篡改：长度不同会先命中 size_mismatch，测不到哈希那一道
    const bad = makePackage('1.0.0', {
      mutateEntries: (entries) => entries.map((e) => (e.name === 'web/app.js'
        ? { name: e.name, data: Buffer.from('// 1.0.X\n') } : e)),
    });
    const p = path.join(os.tmpdir(), `tl-tamper-${Date.now()}.tlpkg`);
    fs.writeFileSync(p, bad.buffer);
    let r = null;
    try { mf.verifyPackage(zipLib.open(p), mf.validateManifest(bad.manifest)); } catch (e) { r = e.reason; }
    eq(r, 'hash_mismatch');
  });
}

console.log('6. 端到端：安装 / 幂等 / 降级 / 篡改');
const e2eRoot = freshUserData('e2e');
const srcDir = makeSource(path.join(e2eRoot, 'source'), [
  { version: '0.6.0', file: 'tracelens-0.6.0.tlpkg', ...makePackage('0.6.0') },
  { version: '0.6.1', file: 'tracelens-0.6.1.tlpkg', ...makePackage('0.6.1') },
]);

await checkAsync('未配置更新源 → source_not_configured', async () => {
  const up = createUpdater({ userDataDir: path.join(e2eRoot, 'ud0'), appVersion: '0.6.0' });
  const res = await up.check();
  eq(res.ok, false);
  eq(res.reason, 'source_not_configured');
});

await checkAsync('源目录不存在 → 明确失败，不报"已是最新"', async () => {
  const up = createUpdater({ userDataDir: path.join(e2eRoot, 'ud1'), appVersion: '0.6.0' });
  const res = await up.check({ source: path.join(e2eRoot, 'no-such-dir') });
  eq(res.ok, false);
  ok(res.reason !== 'up_to_date', '不能把不可达当成最新');
});

const ud2 = path.join(e2eRoot, 'ud2');
await checkAsync('首次安装 0.6.1 → 激活指针写入且待确认', async () => {
  const up = createUpdater({ userDataDir: ud2, appVersion: '0.6.0' });
  const res = await up.install({ source: srcDir });
  eq(res.ok, true);
  eq(res.version, '0.6.1');
  eq(res.requiresRestart, true);
  const st = up.readState();
  eq(st.version, '0.6.1');
  eq(st.pending_confirm, true);
  const dirs = up.activeDirs();
  ok(dirs.webDir && fs.existsSync(path.join(dirs.webDir, 'app.js')), 'web 目录必须落盘');
  eq(fs.readFileSync(path.join(dirs.webDir, 'app.js'), 'utf8'), '// 0.6.1\n');
});

await checkAsync('同版本重复安装 → already_installed（幂等，不重装）', async () => {
  const up = createUpdater({ userDataDir: ud2, appVersion: '0.6.0' });
  const res = await up.install({ source: srcDir });
  eq(res.ok, false);
  eq(res.reason, 'already_installed');
});

await checkAsync('降级被拒；显式 allowDowngrade 才过', async () => {
  const up = createUpdater({ userDataDir: ud2, appVersion: '0.6.0' });
  const refused = await up.install({ source: srcDir, version: '0.6.0' });
  eq(refused.ok, false);
  eq(refused.reason, 'downgrade_refused');
  const allowed = await up.install({ source: srcDir, version: '0.6.0', allowDowngrade: true });
  eq(allowed.ok, true);
  eq(up.readState().version, '0.6.0');
  eq(up.readState().previous, '0.6.1');
});

await checkAsync('确认健康 → pending_confirm 清掉', async () => {
  const up = createUpdater({ userDataDir: ud2, appVersion: '0.6.0' });
  const res = await up.install({ source: srcDir, version: '0.6.1' });
  eq(res.ok, true);
  eq(up.readState().pending_confirm, true);
  up.confirmHealthy();
  eq(up.readState().pending_confirm, false);
});

await checkAsync('index 里包哈希被改 → package_hash_mismatch（解压前就挡住）', async () => {
  const tampered = path.join(e2eRoot, 'src-tampered');
  fs.mkdirSync(tampered, { recursive: true });
  const pkg = makePackage('0.6.2');
  fs.writeFileSync(path.join(tampered, 'tracelens-0.6.2.tlpkg'), pkg.buffer);
  fs.writeFileSync(path.join(tampered, 'index.json'), JSON.stringify({
    schema: 'tracelens.update-index/1',
    channel: 'stable',
    latest: '0.6.2',
    releases: [{
      version: '0.6.2', file: 'tracelens-0.6.2.tlpkg', size: pkg.buffer.length,
      sha256: '0'.repeat(64), capabilities: ['web'], min_host: null, notes: '',
    }],
  }, null, 2));
  const up = createUpdater({ userDataDir: path.join(e2eRoot, 'ud3'), appVersion: '0.6.1' });
  const res = await up.install({ source: tampered });
  eq(res.ok, false);
  eq(res.reason, 'package_hash_mismatch');
  eq(up.readState(), null, '失败时激活指针不能动');
});

await checkAsync('包内文件被改 → hash_mismatch，且不留下版本目录', async () => {
  const dir = path.join(e2eRoot, 'src-badfile');
  fs.mkdirSync(dir, { recursive: true });
  const bad = makePackage('0.6.3', {
    mutateEntries: (entries) => entries.map((e) => (e.name === 'web/app.js'
      ? { name: e.name, data: Buffer.from('// evil!\n') } : e)),
  });
  fs.writeFileSync(path.join(dir, 'tracelens-0.6.3.tlpkg'), bad.buffer);
  fs.writeFileSync(path.join(dir, 'index.json'), JSON.stringify({
    schema: 'tracelens.update-index/1', channel: 'stable', latest: '0.6.3',
    releases: [{ version: '0.6.3', file: 'tracelens-0.6.3.tlpkg', size: bad.buffer.length,
      sha256: sha256(bad.buffer), capabilities: ['web'], min_host: null, notes: '' }],
  }, null, 2));
  const ud = path.join(e2eRoot, 'ud4');
  const up = createUpdater({ userDataDir: ud, appVersion: '0.6.1' });
  const res = await up.install({ source: dir });
  eq(res.ok, false);
  eq(res.reason, 'hash_mismatch');
  ok(!fs.existsSync(path.join(ud, 'updates', 'versions', '0.6.3')), '坏包不能留下版本目录');
});

await checkAsync('宿主缺少 runtime 能力 → 拒绝，不装半个', async () => {
  const dir = path.join(e2eRoot, 'src-rt');
  fs.mkdirSync(dir, { recursive: true });
  const pkg = makePackage('0.6.4', {
    capabilities: ['web', 'runtime'],
    files: {
      'web/index.html': 'x',
      'runtime/agent_explainer/server.py': 'print(1)\n',
    },
  });
  fs.writeFileSync(path.join(dir, 'tracelens-0.6.4.tlpkg'), pkg.buffer);
  fs.writeFileSync(path.join(dir, 'index.json'), JSON.stringify({
    schema: 'tracelens.update-index/1', channel: 'stable', latest: '0.6.4',
    releases: [{ version: '0.6.4', file: 'tracelens-0.6.4.tlpkg', size: pkg.buffer.length,
      sha256: sha256(pkg.buffer), capabilities: ['web', 'runtime'], min_host: null, notes: '' }],
  }, null, 2));
  const up = createUpdater({ userDataDir: path.join(e2eRoot, 'ud5'), appVersion: '0.6.1', capabilities: ['web'] });
  const res = await up.install({ source: dir });
  eq(res.ok, false);
  eq(res.reason, 'host_missing_capability');
});

await checkAsync('min_host 高于宿主 → host_too_old', async () => {
  const dir = path.join(e2eRoot, 'src-minhost');
  fs.mkdirSync(dir, { recursive: true });
  const pkg = makePackage('0.6.5', { mutateManifest: (m) => { m.min_host = '0.9.0'; } });
  fs.writeFileSync(path.join(dir, 'tracelens-0.6.5.tlpkg'), pkg.buffer);
  fs.writeFileSync(path.join(dir, 'index.json'), JSON.stringify({
    schema: 'tracelens.update-index/1', channel: 'stable', latest: '0.6.5',
    releases: [{ version: '0.6.5', file: 'tracelens-0.6.5.tlpkg', size: pkg.buffer.length,
      sha256: sha256(pkg.buffer), capabilities: ['web'], min_host: '0.9.0', notes: '' }],
  }, null, 2));
  const up = createUpdater({ userDataDir: path.join(e2eRoot, 'ud6'), appVersion: '0.6.1' });
  const res = await up.install({ source: dir });
  eq(res.ok, false);
  eq(res.reason, 'host_too_old');
});

await checkAsync('本地源不可达时不退回缓存（路径写错 ≠ 网络抖动）', async () => {
  const ud = path.join(e2eRoot, 'ud10');
  const up = createUpdater({ userDataDir: ud, appVersion: '0.6.0' });
  const first = await up.check({ source: srcDir });   // 先成功一次，把 index 写进缓存
  eq(first.ok, true);
  ok(fs.existsSync(up.paths.indexCache), '成功拉取后应留下缓存');
  const res = await up.check({ source: path.join(e2eRoot, 'gone-dir') });
  eq(res.ok, false, '本地目录读不到就是失败，不能拿缓存冒充');
  eq(res.reason, 'source_unreachable');
});

console.log('7. 启动失败自动回滚');
await checkAsync('pending_confirm + 有上一版 → 计划回退', async () => {
  const ud = path.join(e2eRoot, 'ud7');
  const up = createUpdater({ userDataDir: ud, appVersion: '0.6.0' });
  await up.install({ source: srcDir, version: '0.6.0' });
  up.confirmHealthy();
  await up.install({ source: srcDir, version: '0.6.1' });
  const plan = up.planRollback('startup_failed');
  eq(plan.needed, true);
  eq(plan.to, '0.6.0');
  const applied = up.applyRollback(plan);
  eq(applied.ok, true);
  eq(up.readState().version, '0.6.0');
  eq(up.activeDirs().version, '0.6.0');
});

await checkAsync('回退只做一次（防止启动风暴）', async () => {
  const ud = path.join(e2eRoot, 'ud7');
  const up = createUpdater({ userDataDir: ud, appVersion: '0.6.0' });
  const plan = up.planRollback('startup_failed_again');
  eq(plan.needed, false);
  eq(plan.reason, 'already_confirmed');
});

await checkAsync('已确认过的版本崩了 → 不擅自换版本', async () => {
  const ud = path.join(e2eRoot, 'ud8');
  const up = createUpdater({ userDataDir: ud, appVersion: '0.6.0' });
  await up.install({ source: srcDir, version: '0.6.0' });
  up.confirmHealthy();
  const plan = up.planRollback('runtime_crash');
  eq(plan.needed, false);
  eq(plan.reason, 'already_confirmed');
});

await checkAsync('没有上一版 → 不回退，如实说没有', async () => {
  const ud = path.join(e2eRoot, 'ud9');
  const up = createUpdater({ userDataDir: ud, appVersion: '0.6.0' });
  await up.install({ source: srcDir, version: '0.6.0' });
  const plan = up.planRollback('startup_failed');
  eq(plan.needed, false);
  eq(plan.reason, 'no_previous_version');
});

console.log('8. 状态与清理');
await checkAsync('status 反映激活版本与安装列表', async () => {
  const up = createUpdater({ userDataDir: path.join(e2eRoot, 'ud7'), appVersion: '0.6.0' });
  const st = up.status();
  eq(st.active, '0.6.0');
  ok(st.installed.includes('0.6.0') && st.installed.includes('0.6.1'), '两个版本都该在列表里');
  eq(st.hostVersion, '0.6.0');
});

await check('不能删除正在使用的版本', () => {
  const up = createUpdater({ userDataDir: path.join(e2eRoot, 'ud7'), appVersion: '0.6.0' });
  const res = up.removeInstalled('0.6.0');
  eq(res.ok, false);
  eq(res.reason, 'version_in_use');
});

await check('能删除非激活版本', () => {
  const up = createUpdater({ userDataDir: path.join(e2eRoot, 'ud7'), appVersion: '0.6.0' });
  const res = up.removeInstalled('0.6.1');
  eq(res.ok, true);
});

await check('事务日志留下了 install / rollback 记录', () => {
  const up = createUpdater({ userDataDir: path.join(e2eRoot, 'ud7'), appVersion: '0.6.0' });
  const text = fs.readFileSync(up.paths.events, 'utf8');
  ok(text.includes('"type":"install"'), '要有 install 事件');
  ok(text.includes('"type":"rollback"'), '要有 rollback 事件');
});

// --------------------------------------------------------------------------- #
console.log('');
if (failures.length) {
  console.log(`✗ ${failures.length} 项失败 / ${passed} 项通过`);
  for (const f of failures) {
    console.log(`  - ${f.title}: ${f.message}`);
  }
  process.exit(1);
}
console.log(`✓ 全部通过：${passed} 项`);
process.exit(0);
