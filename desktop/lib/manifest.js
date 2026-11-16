'use strict';
/**
 * 更新包的 manifest 校验与路径安全（更新通道的"判据层"）。
 *
 * 这里的每条规则都对应一种真实的坏结果，不是教条：
 *  * 路径穿越 → 解压时把文件写到 `updates/versions/../../..`，等于把用户的
 *    AppData 当垃圾场，极端情况下能覆盖启动项。
 *  * 条目集合与 manifest 不符 → "包里多一个文件"这种差异如果放过，
 *    逐文件哈希就形同虚设（攻击者只需把恶意文件加进去而不申报）。
 *  * 版本号非法 → 后续所有比较都不可信，所以校验一次、全局按已校验的值用。
 */

const crypto = require('node:crypto');

const PACKAGE_SCHEMA = 'tracelens.update-package/1';
const INDEX_SCHEMA = 'tracelens.update-index/1';
const STATE_SCHEMA = 'tracelens.update-state/1';
const CAPABILITIES = new Set(['web', 'runtime']);

class ManifestError extends Error {
  constructor(reason, message) {
    super(message);
    this.name = 'ManifestError';
    this.reason = reason;
  }
}

function sha256(buf) {
  return crypto.createHash('sha256').update(buf).digest('hex');
}

/**
 * 解压路径安全判定。
 *
 * 允许：`web/app.js`、`runtime/agent_explainer/server.py`、中文文件名。
 * 拒绝：`/etc/passwd`、`C:/x`、`..\\x`、`a/../../b`、`web//app.js`、结尾空格/点
 *      （Windows 会静默裁掉，导致校验的文件名与实际写入的不是同一个）。
 */
function isSafeEntryPath(name) {
  if (typeof name !== 'string' || name.length === 0 || name.length > 512) {
    return false;
  }
  if (name.includes('\\')) {
    return false; // 只用正斜杠，避免 Windows/POSIX 语义分歧
  }
  if (name.startsWith('/')) {
    return false;
  }
  if (/^[A-Za-z]:/.test(name)) {
    return false;
  }
  if (/[\u0000-\u001f]/.test(name)) {
    return false;
  }
  const parts = name.split('/');
  for (const part of parts) {
    if (part === '' || part === '.' || part === '..') {
      return false;
    }
    if (/[ .]$/.test(part)) {
      return false; // 结尾空格或点
    }
  }
  return true;
}

/** 顶层必须只有这几样（防止包把东西塞到 versions/<ver>/ 的根上） */
const ALLOWED_TOP_LEVEL = new Set(['manifest.json', 'web', 'runtime']);

function topLevelOf(name) {
  const i = name.indexOf('/');
  return i < 0 ? name : name.slice(0, i);
}

function assertObject(value, reason, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new ManifestError(reason, `${label} 必须是对象`);
  }
}

/**
 * 校验 manifest 结构。返回**规范化后的副本**（调用方只应使用返回值，
 * 不要回头再用原始对象——避免"校验的是 A、用的是 B"）。
 */
function validateManifest(raw) {
  assertObject(raw, 'bad_manifest', 'manifest');
  if (raw.schema !== PACKAGE_SCHEMA) {
    throw new ManifestError('bad_schema',
      `manifest.schema 必须是 ${PACKAGE_SCHEMA}，实际 ${JSON.stringify(raw.schema)}`);
  }
  const versionLib = require('./version'); // 延迟 require，避免循环
  if (typeof raw.version !== 'string' || !versionLib.isValid(raw.version)) {
    throw new ManifestError('bad_version', `manifest.version 不合法：${JSON.stringify(raw.version)}`);
  }
  // null 是「不限制」的合法写法（打包器默认就写 null）；只有给了非空值才校验格式
  if (raw.min_host !== undefined && raw.min_host !== null
      && (typeof raw.min_host !== 'string' || !versionLib.isValid(raw.min_host))) {
    throw new ManifestError('bad_min_host', `manifest.min_host 不合法：${JSON.stringify(raw.min_host)}`);
  }
  const capabilities = raw.capabilities;
  if (!Array.isArray(capabilities) || capabilities.length === 0) {
    throw new ManifestError('bad_capabilities', 'manifest.capabilities 必须是非空数组');
  }
  for (const cap of capabilities) {
    if (!CAPABILITIES.has(cap)) {
      throw new ManifestError('unknown_capability', `未知能力：${JSON.stringify(cap)}`);
    }
  }
  if (!capabilities.includes('web')) {
    throw new ManifestError('missing_web', 'manifest.capabilities 必须包含 "web"（界面是更新包的最小单位）');
  }
  if (!Array.isArray(raw.files) || raw.files.length === 0) {
    throw new ManifestError('bad_files', 'manifest.files 必须是非空数组');
  }
  const seen = new Set();
  const files = raw.files.map((item, i) => {
    assertObject(item, 'bad_files', `manifest.files[${i}]`);
    const { path: p, size, sha256: digest } = item;
    if (typeof p !== 'string' || !isSafeEntryPath(p)) {
      throw new ManifestError('unsafe_path', `manifest.files[${i}].path 不安全：${JSON.stringify(p)}`);
    }
    if (p === 'manifest.json') {
      throw new ManifestError('self_reference', 'manifest.files 不得包含 manifest.json 自身');
    }
    if (!ALLOWED_TOP_LEVEL.has(topLevelOf(p))) {
      throw new ManifestError('bad_top_level',
        `manifest.files[${i}].path 落在不认识的顶层目录：${p}（只允许 web/ 与 runtime/）`);
    }
    if (!Number.isInteger(size) || size < 0) {
      throw new ManifestError('bad_size', `manifest.files[${i}].size 必须是非负整数`);
    }
    if (typeof digest !== 'string' || !/^[0-9a-f]{64}$/.test(digest)) {
      throw new ManifestError('bad_sha256', `manifest.files[${i}].sha256 必须是 64 位小写十六进制`);
    }
    if (seen.has(p)) {
      throw new ManifestError('duplicate_path', `manifest.files 出现重复路径：${p}`);
    }
    seen.add(p);
    return { path: p, size, sha256: digest };
  });
  return {
    schema: PACKAGE_SCHEMA,
    version: raw.version,
    channel: typeof raw.channel === 'string' ? raw.channel : 'stable',
    min_host: raw.min_host || null,
    capabilities: capabilities.slice(),
    created_at: typeof raw.created_at === 'string' ? raw.created_at : null,
    notes: typeof raw.notes === 'string' ? raw.notes : '',
    files,
  };
}

/**
 * 拿包内实际条目与 manifest 对账，并逐文件核对 sha256。
 *
 * `zip` 是 `lib/zip.js` 返回的句柄。返回 {checked, bytes}。
 * 任何不一致都抛 ManifestError，**不做部分安装**。
 */
function verifyPackage(zip, manifest) {
  const declared = new Map(manifest.files.map((f) => [f.path, f]));
  const actual = new Map();
  for (const name of zip.names) {
    if (name.endsWith('/')) {
      continue; // 目录条目不参与对账
    }
    if (name === 'manifest.json') {
      continue;
    }
    actual.set(name, true);
  }

  const missing = [...declared.keys()].filter((p) => !actual.has(p));
  if (missing.length) {
    throw new ManifestError('missing_entries',
      `包里缺少 manifest 声明的文件（${missing.length} 个）：${missing.slice(0, 5).join(', ')}`);
  }
  const extra = [...actual.keys()].filter((p) => !declared.has(p));
  if (extra.length) {
    throw new ManifestError('undeclared_entries',
      `包里存在 manifest 未声明的文件（${extra.length} 个）：${extra.slice(0, 5).join(', ')}`);
  }

  let bytes = 0;
  for (const [p, item] of declared) {
    const buf = zip.read(p);
    if (buf.length !== item.size) {
      throw new ManifestError('size_mismatch',
        `${p} 大小不符：manifest ${item.size}，实际 ${buf.length}`);
    }
    const digest = sha256(buf);
    if (digest !== item.sha256) {
      throw new ManifestError('hash_mismatch',
        `${p} 哈希不符：manifest ${item.sha256.slice(0, 12)}…，实际 ${digest.slice(0, 12)}…`);
    }
    bytes += buf.length;
  }
  return { checked: declared.size, bytes };
}

/** 能力门禁：包里要求 runtime，但宿主给不出 runtime 支持 → 拒绝（不能装半个）。 */
function assertHostSupports(manifest, host) {
  const caps = new Set(host && host.capabilities ? host.capabilities : ['web']);
  for (const cap of manifest.capabilities) {
    if (!caps.has(cap)) {
      throw new ManifestError('host_missing_capability',
        `这个更新包需要宿主支持「${cap}」，当前 TraceLens 不支持。`
        + '请先安装完整安装包，再回到这里更新。');
    }
  }
  if (manifest.min_host) {
    const versionLib = require('./version');
    if (versionLib.compare(host.appVersion, manifest.min_host) < 0) {
      throw new ManifestError('host_too_old',
        `这个更新包要求 TraceLens 至少 ${manifest.min_host}，当前 ${host.appVersion}。`
        + '请先安装完整安装包。');
    }
  }
}

module.exports = {
  PACKAGE_SCHEMA,
  INDEX_SCHEMA,
  STATE_SCHEMA,
  CAPABILITIES,
  ALLOWED_TOP_LEVEL,
  ManifestError,
  sha256,
  isSafeEntryPath,
  topLevelOf,
  validateManifest,
  verifyPackage,
  assertHostSupports,
};
