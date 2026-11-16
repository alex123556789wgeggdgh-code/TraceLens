'use strict';
/**
 * TraceLens 更新引擎（0.6.0 更新通道）。
 *
 * 设计契约见 `docs/21-update-channel.md`，这里只强调三条实现纪律：
 *
 *  1. **一切写入都是"先写临时、再 rename"**：`current.json` 与 `versions/<ver>/`
 *     都走这条路。半成品目录永远不会被当成可用版本——因为激活指针只会指向
 *     一个 rename 完成后的目录。
 *  2. **失败即整包作废，不做部分安装**。任何一步校验不过，staging 与
 *     `versions/<ver>` 全部删掉，`current.json` 一字不动。
 *  3. **绝不假成功**。所有返回值都带 `reason` 码，没有网络、源不存在、包损坏、
 *     宿主太旧各有各的码，界面按码说人话。
 *
 * 这个模块不 import electron，纯 Node，方便在无 GUI 环境下被测试直接调用。
 */

const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const https = require('node:https');
const { fileURLToPath, pathToFileURL } = require('node:url');

const version = require('./lib/version');
const zip = require('./lib/zip');
const mf = require('./lib/manifest');

const DEFAULT_HTTP_TIMEOUT_MS = 20000;
const DEFAULT_MAX_PACKAGE_BYTES = 512 * 1024 * 1024;
const EVENT_LOG_MAX_BYTES = 512 * 1024;

/** 更新源可以是本地路径、file:// 或 http(s)://；统一成 {kind, base}。 */
function parseSource(source) {
  if (typeof source !== 'string' || source.trim() === '') {
    return { kind: 'none', base: '', reason: 'source_not_configured' };
  }
  const raw = source.trim();
  if (/^https?:\/\//i.test(raw)) {
    return { kind: 'http', base: raw.endsWith('/') ? raw : `${raw}/` };
  }
  if (/^file:\/\//i.test(raw)) {
    try {
      return { kind: 'file', base: fileURLToPath(raw.endsWith('/') ? raw : `${raw}/`) };
    } catch (err) {
      return { kind: 'invalid', base: raw, reason: 'bad_source_url' };
    }
  }
  if (/^[A-Za-z]:[\\/]/.test(raw) || raw.startsWith('/') || raw.startsWith('\\\\')) {
    return { kind: 'file', base: raw };
  }
  return { kind: 'invalid', base: raw, reason: 'unsupported_source_scheme' };
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

/** 原子写 JSON：同目录临时文件 → rename（跨设备失败时退回 copy + unlink）。 */
function writeJsonAtomic(target, value) {
  ensureDir(path.dirname(target));
  const tmp = `${target}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
  try {
    fs.renameSync(tmp, target);
  } catch (err) {
    fs.copyFileSync(tmp, target);
    fs.unlinkSync(tmp);
  }
}

function readJson(target, fallback) {
  try {
    return JSON.parse(fs.readFileSync(target, 'utf8'));
  } catch (err) {
    return fallback;
  }
}

function rmrf(target) {
  try {
    fs.rmSync(target, { recursive: true, force: true });
  } catch (err) {
    // 删不掉不算致命：下次安装会用新的 staging 目录，不会读到半成品
  }
}

/** 简单 HTTP(S) GET，跟随最多 3 次重定向；返回 Buffer。 */
function httpGetBuffer(url, { timeoutMs = DEFAULT_HTTP_TIMEOUT_MS, maxBytes = DEFAULT_MAX_PACKAGE_BYTES, redirects = 3 } = {}) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('https:') ? https : http;
    const req = client.get(url, { timeout: timeoutMs }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        if (redirects <= 0) {
          reject(Object.assign(new Error('重定向次数过多'), { reason: 'too_many_redirects' }));
          return;
        }
        const next = new URL(res.headers.location, url).toString();
        httpGetBuffer(next, { timeoutMs, maxBytes, redirects: redirects - 1 }).then(resolve, reject);
        return;
      }
      if (res.statusCode !== 200) {
        res.resume();
        reject(Object.assign(new Error(`HTTP ${res.statusCode}`), {
          reason: 'http_status', status: res.statusCode,
        }));
        return;
      }
      const chunks = [];
      let total = 0;
      res.on('data', (chunk) => {
        total += chunk.length;
        if (total > maxBytes) {
          req.destroy(Object.assign(new Error('响应体超过上限'), { reason: 'too_large' }));
          return;
        }
        chunks.push(chunk);
      });
      res.on('end', () => resolve(Buffer.concat(chunks)));
      res.on('error', (err) => reject(Object.assign(err, { reason: 'read_failed' })));
    });
    req.on('timeout', () => req.destroy(Object.assign(new Error('请求超时'), { reason: 'timeout' })));
    req.on('error', (err) => reject(Object.assign(err, { reason: err.reason || 'network_error' })));
  });
}

/** 统一取数：`file://` 走本地文件，`http(s)://` 走网络。 */
async function defaultFetch(url) {
  if (/^file:\/\//i.test(url)) {
    return fs.promises.readFile(fileURLToPath(url));
  }
  return httpGetBuffer(url);
}

/**
 * 创建更新引擎。
 *
 * @param {object} options
 * @param {string} options.userDataDir   Electron userData（`%APPDATA%/TraceLens`）
 * @param {string} options.appVersion    宿主版本（用于 min_host 与降级判定）
 * @param {string[]} [options.capabilities] 宿主能力，默认 ['web']
 * @param {function} [options.log]       日志回调
 * @param {function} [options.fetchBuffer] 注入式取数（测试用），签名 (url) => Promise<Buffer>
 */
function createUpdater(options) {
  const userDataDir = options.userDataDir;
  const appVersion = options.appVersion;
  const capabilities = options.capabilities || ['web'];
  const log = options.log || (() => {});
  const fetchBuffer = options.fetchBuffer || defaultFetch;

  const root = path.join(userDataDir, 'updates');
  const P = {
    root,
    versions: path.join(root, 'versions'),
    staging: path.join(root, 'staging'),
    current: path.join(root, 'current.json'),
    config: path.join(root, 'config.json'),
    indexCache: path.join(root, 'index-cache.json'),
    events: path.join(root, 'events.jsonl'),
  };

  function event(type, detail) {
    try {
      ensureDir(root);
      const line = `${JSON.stringify(Object.assign({ at: new Date().toISOString(), type }, detail))}\n`;
      fs.appendFileSync(P.events, line, 'utf8');
      const stat = fs.statSync(P.events);
      if (stat.size > EVENT_LOG_MAX_BYTES) {
        const keep = fs.readFileSync(P.events, 'utf8').split('\n').slice(-200).join('\n');
        fs.writeFileSync(P.events, `${keep}\n`, 'utf8');
      }
    } catch (err) {
      // 事件日志写不进去不能影响主流程
    }
    log(`[update] ${type} ${JSON.stringify(detail)}`);
  }

  function readConfig() {
    const cfg = readJson(P.config, null);
    return Object.assign({
      schema: 'tracelens.update-config/1',
      source: null,
      autoCheck: false,
      channel: 'stable',
    }, cfg && typeof cfg === 'object' ? cfg : {});
  }

  function writeConfig(patch) {
    const next = Object.assign(readConfig(), patch, {
      schema: 'tracelens.update-config/1',
      updated_at: new Date().toISOString(),
    });
    writeJsonAtomic(P.config, next);
    return next;
  }

  /** 读激活指针；没有则返回 null（= 用安装包内置版本，热更未启用）。 */
  function readState() {
    const st = readJson(P.current, null);
    if (!st || typeof st !== 'object' || typeof st.version !== 'string') {
      return null;
    }
    return Object.assign({ schema: mf.STATE_SCHEMA }, st);
  }

  function writeState(next) {
    writeJsonAtomic(P.current, Object.assign({ schema: mf.STATE_SCHEMA }, next));
  }

  /** 已安装版本目录（含 manifest.json 的才算安装完整）。 */
  function installedVersions() {
    try {
      return fs.readdirSync(P.versions, { withFileTypes: true })
        .filter((d) => d.isDirectory())
        .map((d) => d.name)
        .filter((name) => version.isValid(name))
        .filter((name) => fs.existsSync(path.join(P.versions, name, 'manifest.json')))
        .sort((a, b) => version.compare(a, b));
    } catch (err) {
      return [];
    }
  }

  function versionDir(v) {
    return path.join(P.versions, v);
  }

  /**
   * 当前生效的界面目录 / Python 业务包目录。
   * 没有激活版本 → 返回 null，调用方（主进程）应继续用安装包内置资源。
   */
  function activeDirs() {
    const st = readState();
    if (!st) {
      return { version: null, webDir: null, runtimeDir: null, pendingConfirm: false };
    }
    const dir = versionDir(st.version);
    const webDir = path.join(dir, 'web');
    const runtimeDir = path.join(dir, 'runtime');
    if (!fs.existsSync(path.join(dir, 'manifest.json'))) {
      return { version: st.version, webDir: null, runtimeDir: null, pendingConfirm: !!st.pending_confirm, broken: true };
    }
    return {
      version: st.version,
      webDir: fs.existsSync(webDir) ? webDir : null,
      runtimeDir: fs.existsSync(runtimeDir) ? runtimeDir : null,
      pendingConfirm: !!st.pending_confirm,
    };
  }

  /** 取 index.json（先试网络，失败退回本地缓存，并如实标注来源）。 */
  async function fetchIndex(sourceOverride) {
    const cfg = readConfig();
    const source = sourceOverride !== undefined ? sourceOverride : cfg.source;
    const parsed = parseSource(source);
    if (parsed.kind === 'none') {
      return { ok: false, reason: 'source_not_configured', message: '还没有配置更新源' };
    }
    if (parsed.kind === 'invalid') {
      return { ok: false, reason: parsed.reason, message: `更新源地址无法识别：${parsed.base}` };
    }

    const candidates = parsed.kind === 'file'
      ? [path.join(parsed.base, 'index.json'), path.join(parsed.base, 'index.jsonl')]
      : [`${parsed.base}index.json`];

    let lastErr = null;
    for (const target of candidates) {
      // Windows 盘符路径必须走 pathToFileURL：手拼 `file://E:/x` 会被当成
      // "主机名 = E"，fileURLToPath 之后变成 `/x`，读的是系统盘根目录。
      const url = parsed.kind === 'file' ? pathToFileURL(target).toString() : target;
      try {
        const buf = await fetchBuffer(url);
        const doc = JSON.parse(buf.toString('utf8'));
        if (!doc || doc.schema !== mf.INDEX_SCHEMA) {
          return { ok: false, reason: 'bad_index_schema', message: `index.json 的 schema 不是 ${mf.INDEX_SCHEMA}` };
        }
        if (!Array.isArray(doc.releases)) {
          return { ok: false, reason: 'bad_index', message: 'index.json 缺少 releases 数组' };
        }
        ensureDir(root);
        writeJsonAtomic(P.indexCache, Object.assign({ fetched_at: new Date().toISOString() }, doc));
        return { ok: true, index: doc, source: parsed, fromCache: false };
      } catch (err) {
        lastErr = err;
      }
    }
    // 只有**网络**源才退回缓存：网线断了是临时故障，用上次拉到的清单顶一下有意义。
    // 本地目录读不到几乎只有一个原因——路径写错了。那种情况退缓存会把"配置错了"
    // 包装成"源正常（来自缓存）"，用户照着旧版本号点安装，只会得到第二个错。
    const cached = parsed.kind === 'http' ? readJson(P.indexCache, null) : null;
    if (cached && Array.isArray(cached.releases)) {
      return {
        ok: true, index: cached, source: parsed, fromCache: true,
        warning: `更新源不可达（${lastErr && lastErr.message}），以下内容来自 ${cached.fetched_at} 的本地缓存`,
      };
    }
    const reasonCode = (lastErr && lastErr.reason) || 'source_unreachable';
    return {
      ok: false,
      reason: reasonCode,
      message: `更新源不可达：${lastErr ? lastErr.message : parsed.base}`,
    };
  }

  /** 在 index 里挑出可装的最新一条（不信任 latest 字段，逐条按 version 排序）。 */
  function pickRelease(index, { allowPrerelease = false } = {}) {
    const rows = (index.releases || []).filter((r) => r && typeof r.version === 'string' && version.isValid(r.version));
    const usable = allowPrerelease ? rows : rows.filter((r) => version.parse(r.version).pre.length === 0);
    if (!usable.length) {
      return null;
    }
    usable.sort((a, b) => version.compare(a.version, b.version));
    return usable[usable.length - 1];
  }

  /** 只查：返回当前版本 / 可装版本 / 是否可升。 */
  async function check({ source } = {}) {
    const state = readState();
    const current = state ? state.version : null;
    const res = await fetchIndex(source);
    if (!res.ok) {
      return { ok: false, reason: res.reason, message: res.message, current, hostVersion: appVersion };
    }
    const release = pickRelease(res.index);
    if (!release) {
      return {
        ok: true, current, hostVersion: appVersion, latest: null, available: false,
        message: '更新源里没有适用于当前通道的版本', fromCache: res.fromCache, warning: res.warning,
      };
    }
    const available = current === null || version.compare(release.version, current) > 0;
    return {
      ok: true,
      current,
      hostVersion: appVersion,
      latest: release.version,
      available,
      release,
      all: (res.index.releases || []).map((r) => r.version),
      installed: installedVersions(),
      fromCache: res.fromCache,
      warning: res.warning,
      notes: release.notes || '',
      capabilities: release.capabilities || [],
    };
  }

  /** 把包下载/拷贝到 staging 目录（<staging>/pkg.tlpkg）。 */
  async function fetchPackage(parsed, release) {
    const file = release.file;
    if (typeof file !== 'string' || !/^[A-Za-z0-9._-]+$/.test(file)) {
      throw Object.assign(new Error(`index 里的文件名不合法：${JSON.stringify(file)}`),
        { reason: 'bad_release_file' });
    }
    const url = parsed.kind === 'file'
      ? pathToFileURL(path.join(parsed.base, file)).toString()
      : `${parsed.base}${file}`;
    const buf = await fetchBuffer(url);
    if (typeof release.size === 'number' && release.size !== buf.length) {
      throw Object.assign(new Error(`包大小不符：index ${release.size}，实际 ${buf.length}`),
        { reason: 'package_size_mismatch' });
    }
    if (typeof release.sha256 === 'string' && release.sha256) {
      const digest = mf.sha256(buf);
      if (digest !== release.sha256.toLowerCase()) {
        throw Object.assign(new Error(`包哈希不符：index ${release.sha256.slice(0, 12)}…，实际 ${digest.slice(0, 12)}…`),
          { reason: 'package_hash_mismatch' });
      }
    }
    return buf;
  }

  /** 解压到 staging/<ver>.tmp，再 rename 成 versions/<ver>。 */
  function materialize(pkgPath, manifest) {
    ensureDir(P.versions);
    ensureDir(P.staging);
    const finalDir = versionDir(manifest.version);
    const tmpDir = path.join(P.staging, `${manifest.version}.tmp-${Date.now()}`);
    rmrf(tmpDir);
    ensureDir(tmpDir);

    const handle = zip.open(pkgPath);
    try {
      for (const item of manifest.files) {
        const dest = path.join(tmpDir, ...item.path.split('/'));
        // 二次确认：即便 manifest 已校验过，落盘前再判一次（防"校验后被改"这类糊涂账）
        if (!mf.isSafeEntryPath(item.path)) {
          throw new mf.ManifestError('unsafe_path', `拒绝不安全的条目路径：${item.path}`);
        }
        const rel = path.relative(tmpDir, dest);
        if (rel.startsWith('..') || path.isAbsolute(rel)) {
          throw new mf.ManifestError('unsafe_path', `条目逃出目标目录：${item.path}`);
        }
        ensureDir(path.dirname(dest));
        fs.writeFileSync(dest, handle.read(item.path));
      }
      fs.writeFileSync(path.join(tmpDir, 'manifest.json'),
        `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
    } finally {
      // handle 没有 close，Buffer 读完全在内存里
    }

    // 落盘后复核：写入的内容必须与 manifest 一致（防止磁盘满导致的静默截断）
    for (const item of manifest.files) {
      const dest = path.join(tmpDir, ...item.path.split('/'));
      const got = mf.sha256(fs.readFileSync(dest));
      if (got !== item.sha256) {
        throw new mf.ManifestError('post_write_hash_mismatch',
          `写入后复核对不上：${item.path}`);
      }
    }

    rmrf(finalDir);
    fs.renameSync(tmpDir, finalDir);
    return finalDir;
  }

  /**
   * 安装一个版本（校验 → 解压 → 激活指针）。
   * 不做重启：重启由主进程决定（GUI 需要用户点）。
   */
  async function install({
    source, version: wanted, allowDowngrade = false, allowPrerelease = false,
  } = {}) {
    const state = readState();
    const current = state ? state.version : null;
    const res = await fetchIndex(source);
    if (!res.ok) {
      return { ok: false, reason: res.reason, message: res.message, current };
    }

    let release = null;
    if (wanted) {
      release = (res.index.releases || []).find((r) => r && r.version === wanted);
      if (!release) {
        return { ok: false, reason: 'release_not_found', message: `更新源里没有 ${wanted} 这个版本`, current };
      }
    } else {
      release = pickRelease(res.index, { allowPrerelease });
      if (!release) {
        return { ok: false, reason: 'no_release', message: '更新源里没有可用版本', current };
      }
    }

    let pkgPath = null;
    try {
      // 1) 版本门禁
      if (current && !allowDowngrade) {
        const cmp = version.compare(release.version, current);
        if (cmp < 0) {
          return {
            ok: false, reason: 'downgrade_refused', current, target: release.version,
            message: `拒绝降级：当前 ${current}，目标 ${release.version}。确需降级请显式允许。`,
          };
        }
        if (cmp === 0 && fs.existsSync(versionDir(release.version))) {
          return {
            ok: false, reason: 'already_installed', current, target: release.version,
            message: `当前已经是 ${current}`,
          };
        }
      }

      // 2) 取包
      const pkgBuf = await fetchPackage(res.source, release);
      ensureDir(P.staging);
      pkgPath = path.join(P.staging, `${release.version}-${Date.now()}.tlpkg`);
      fs.writeFileSync(pkgPath, pkgBuf);

      // 3) 开包 → 读 manifest → 校验结构
      const handle = zip.open(pkgPath);
      if (!handle.has('manifest.json')) {
        return { ok: false, reason: 'missing_manifest', message: '包里没有 manifest.json', current };
      }
      const rawManifest = JSON.parse(handle.read('manifest.json').toString('utf8'));
      const manifest = mf.validateManifest(rawManifest);

      if (manifest.version !== release.version) {
        return {
          ok: false, reason: 'version_mismatch', current,
          message: `包的版本是 ${manifest.version}，index 声明的是 ${release.version}`,
        };
      }

      // 4) 能力门禁（宿主必须真能承载这个包）
      try {
        mf.assertHostSupports(manifest, { appVersion, capabilities });
      } catch (err) {
        return { ok: false, reason: err.reason || 'host_unsupported', message: err.message, current };
      }

      // 5) 逐文件哈希
      let verified;
      try {
        verified = mf.verifyPackage(handle, manifest);
      } catch (err) {
        return { ok: false, reason: err.reason || 'verify_failed', message: err.message, current };
      }

      // 6) 落盘（staging → rename）
      const dir = materialize(pkgPath, manifest);

      // 7) 激活（pending_confirm = 还没被证明能起来）
      const previous = current && current !== manifest.version ? current : (state ? state.previous : null);
      writeState({
        version: manifest.version,
        previous: previous || null,
        activated_at: new Date().toISOString(),
        pending_confirm: true,
        rollback_attempted: false,
        installed_from: res.source.kind,
        files: verified.checked,
        bytes: verified.bytes,
      });
      event('install', { version: manifest.version, previous: previous || null, files: verified.checked });
      return {
        ok: true,
        version: manifest.version,
        previous: previous || null,
        dir,
        files: verified.checked,
        bytes: verified.bytes,
        notes: manifest.notes,
        requiresRestart: true,
      };
    } catch (err) {
      event('install_failed', { version: release.version, reason: err.reason || 'exception', message: err.message });
      return { ok: false, reason: err.reason || 'install_exception', message: err.message, current };
    } finally {
      if (pkgPath) {
        rmrf(pkgPath);
      }
    }
  }

  /** 启动成功后调用：把 pending_confirm 清掉，并更新 previous。 */
  function confirmHealthy() {
    const st = readState();
    if (!st) {
      return { ok: true, changed: false };
    }
    if (st.pending_confirm) {
      writeState(Object.assign({}, st, { pending_confirm: false, confirmed_at: new Date().toISOString() }));
      event('confirm', { version: st.version });
      return { ok: true, changed: true, version: st.version };
    }
    return { ok: true, changed: false, version: st.version };
  }

  /**
   * 启动失败时调用。返回应该回退到的版本（没有可回退的返回 null）。
   *
   * 只在一个条件下回退：当前版本还是 `pending_confirm`（= 从没成功启动过）。
   * 已经确认过的版本崩了，属于运行期问题，不该被更新机制擅自换掉。
   */
  function planRollback(reason) {
    const st = readState();
    if (!st) {
      return { needed: false, reason: 'no_active_version' };
    }
    if (!st.pending_confirm) {
      return { needed: false, reason: 'already_confirmed', version: st.version };
    }
    if (st.rollback_attempted) {
      return { needed: false, reason: 'rollback_already_attempted', version: st.version };
    }
    const target = st.previous && fs.existsSync(path.join(versionDir(st.previous), 'manifest.json'))
      ? st.previous
      : null;
    if (!target) {
      return { needed: false, reason: 'no_previous_version', version: st.version };
    }
    return { needed: true, from: st.version, to: target, reason };
  }

  function applyRollback(plan) {
    if (!plan || !plan.needed) {
      return { ok: false, reason: (plan && plan.reason) || 'not_needed' };
    }
    const st = readState() || {};
    writeState({
      version: plan.to,
      previous: plan.from,
      activated_at: new Date().toISOString(),
      pending_confirm: false,
      rollback_attempted: true,
      rolled_back_from: plan.from,
      rolled_back_at: new Date().toISOString(),
      rolled_back_reason: plan.reason || 'startup_failed',
    });
    event('rollback', { from: plan.from, to: plan.to, reason: plan.reason });
    return { ok: true, version: plan.to, from: plan.from };
  }

  /** 手动回滚到指定版本（或上一版）。不要求 pending_confirm。 */
  function rollback({ to } = {}) {
    const st = readState();
    if (!st) {
      return { ok: false, reason: 'no_active_version', message: '当前没有通过更新通道安装的版本' };
    }
    let target = to || st.previous;
    if (!target) {
      const list = installedVersions().filter((v) => v !== st.version);
      target = list.length ? list[list.length - 1] : null;
    }
    if (!target) {
      return { ok: false, reason: 'no_target', message: '没有可回退的版本' };
    }
    if (!fs.existsSync(path.join(versionDir(target), 'manifest.json'))) {
      return { ok: false, reason: 'target_missing', message: `版本 ${target} 的目录不完整` };
    }
    writeState({
      version: target,
      previous: st.version,
      activated_at: new Date().toISOString(),
      pending_confirm: false,
      rollback_attempted: false,
      rolled_back_from: st.version,
      rolled_back_at: new Date().toISOString(),
      rolled_back_reason: 'manual',
    });
    event('rollback_manual', { from: st.version, to: target });
    return { ok: true, version: target, previous: st.version };
  }

  /** 删除某个已安装版本（不允许删当前激活的）。 */
  function removeInstalled(v) {
    const st = readState();
    if (st && st.version === v) {
      return { ok: false, reason: 'version_in_use', message: `${v} 正在使用，不能删除` };
    }
    if (!version.isValid(v)) {
      return { ok: false, reason: 'bad_version', message: `版本号不合法：${v}` };
    }
    rmrf(versionDir(v));
    event('remove', { version: v });
    return { ok: true };
  }

  function status() {
    const st = readState();
    const dirs = activeDirs();
    return {
      schema: 'tracelens.update-status/1',
      hostVersion: appVersion,
      capabilities,
      source: readConfig().source,
      autoCheck: readConfig().autoCheck,
      active: st ? st.version : null,
      pendingConfirm: !!(st && st.pending_confirm),
      webDir: dirs.webDir,
      runtimeDir: dirs.runtimeDir,
      installed: installedVersions(),
      lastEvent: (() => {
        try {
          const lines = fs.readFileSync(P.events, 'utf8').trim().split('\n');
          return lines.length ? JSON.parse(lines[lines.length - 1]) : null;
        } catch (err) {
          return null;
        }
      })(),
    };
  }

  return {
    paths: P,
    parseSource,
    readConfig,
    writeConfig,
    readState,
    activeDirs,
    installedVersions,
    fetchIndex,
    check,
    install,
    confirmHealthy,
    planRollback,
    applyRollback,
    rollback,
    removeInstalled,
    status,
    _internal: { writeJsonAtomic, readJson, rmrf, httpGetBuffer, pickRelease },
  };
}

module.exports = { createUpdater, parseSource, httpGetBuffer };
