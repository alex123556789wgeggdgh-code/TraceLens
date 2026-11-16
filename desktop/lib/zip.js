'use strict';
/**
 * 纯 Node 的 zip 读取（零第三方依赖）。
 *
 * 为什么自己写：`electron-builder.yml` 明确「本产品运行时零第三方依赖」，
 * 为了解压一个 zip 引入 archiver/adm-zip 会打破这条口径，也会给打包带来
 * `npm query` 子进程问题（同一份注释里记着的 `spawn EPERM`）。
 *
 * 只做**读**，只支持 store(0) 与 deflate(8)，只支持 ≤4 GB 且不含 zip64 的包
 * —— 更新包是源码与静态资源，几十 MB 量级，超出这个范围的包直接拒绝并说明，
 * 而不是"尽力解一半"。
 *
 * 不做 CRC32 校验：调用方会对每个文件做 sha256 比对（比 CRC32 强得多），
 * 重复校验只会让失败原因变模糊。
 */

const fs = require('node:fs');
const zlib = require('node:zlib');

const EOCD_SIG = 0x06054b50;
const CEN_SIG = 0x02014b50;
const LOC_SIG = 0x04034b50;
const MAX_PACKAGE_BYTES = 512 * 1024 * 1024; // 512 MB 硬上限，超出直接拒绝

class ZipError extends Error {
  constructor(reason, message) {
    super(message);
    this.name = 'ZipError';
    this.reason = reason;
  }
}

/** 从尾部向前找 EOCD（注释最长 65535 字节，所以最多回溯 65557）。 */
function findEocd(buf) {
  const min = Math.max(0, buf.length - 65557);
  for (let i = buf.length - 22; i >= min; i -= 1) {
    if (buf.readUInt32LE(i) === EOCD_SIG) {
      return i;
    }
  }
  return -1;
}

/**
 * 读 central directory，返回 [{name, method, compressedSize, size, localOffset, isDir}]。
 * 条目名按 UTF-8 解码（本项目的包由 `tools/make_update_package.py` 产出，路径全 ASCII；
 * 若第三方工具打出 cp437 名，会解成乱码但**不会**绕过路径安全检查——那层是独立判据）。
 */
function readCentralDirectory(buf) {
  const eocd = findEocd(buf);
  if (eocd < 0) {
    throw new ZipError('not_a_zip', '不是 zip 包：找不到中央目录结束标记（EOCD）');
  }
  const total = buf.readUInt16LE(eocd + 10);
  const cdSize = buf.readUInt32LE(eocd + 12);
  const cdOffset = buf.readUInt32LE(eocd + 16);
  if (total === 0xffff || cdSize === 0xffffffff || cdOffset === 0xffffffff) {
    throw new ZipError('zip64_unsupported', '不支持 zip64 包（更新包不应超过 4 GB）');
  }
  if (cdOffset + cdSize > buf.length) {
    throw new ZipError('truncated', '包被截断：中央目录越界');
  }

  const entries = [];
  let p = cdOffset;
  for (let i = 0; i < total; i += 1) {
    if (p + 46 > buf.length || buf.readUInt32LE(p) !== CEN_SIG) {
      throw new ZipError('bad_central_directory', `中央目录第 ${i + 1} 条损坏`);
    }
    const flags = buf.readUInt16LE(p + 8);
    const method = buf.readUInt16LE(p + 10);
    const compressedSize = buf.readUInt32LE(p + 20);
    const size = buf.readUInt32LE(p + 24);
    const nameLen = buf.readUInt16LE(p + 28);
    const extraLen = buf.readUInt16LE(p + 30);
    const commentLen = buf.readUInt16LE(p + 32);
    const localOffset = buf.readUInt32LE(p + 42);
    const rawName = buf.subarray(p + 46, p + 46 + nameLen);
    // bit 11 = 文件名是 UTF-8；未置位时按 UTF-8 解（本项目自产包都是 UTF-8/ASCII）
    const name = rawName.toString('utf8');
    entries.push({
      name,
      method,
      compressedSize,
      size,
      localOffset,
      isDir: name.endsWith('/'),
      encrypted: (flags & 0x1) !== 0,
    });
    p += 46 + nameLen + extraLen + commentLen;
  }
  return entries;
}

/** 取某一条目的原始解压后内容。 */
function extractEntry(buf, entry) {
  if (entry.encrypted) {
    throw new ZipError('encrypted_entry', `条目被加密，无法读取：${entry.name}`);
  }
  const loc = entry.localOffset;
  if (loc + 30 > buf.length || buf.readUInt32LE(loc) !== LOC_SIG) {
    throw new ZipError('bad_local_header', `条目本地头损坏：${entry.name}`);
  }
  const nameLen = buf.readUInt16LE(loc + 26);
  const extraLen = buf.readUInt16LE(loc + 28);
  const start = loc + 30 + nameLen + extraLen;
  const end = start + entry.compressedSize;
  if (end > buf.length) {
    throw new ZipError('truncated', `条目数据越界：${entry.name}`);
  }
  const raw = buf.subarray(start, end);
  if (entry.method === 0) {
    return Buffer.from(raw);
  }
  if (entry.method === 8) {
    try {
      return zlib.inflateRawSync(raw, { maxOutputLength: Math.max(entry.size, 1024) });
    } catch (err) {
      throw new ZipError('inflate_failed', `解压失败：${entry.name}（${err.message}）`);
    }
  }
  throw new ZipError('unsupported_method', `不支持的压缩方式 ${entry.method}：${entry.name}`);
}

/** 打开一个 zip 文件，返回 {entries, read(name), names}。 */
function open(filePath) {
  const stat = fs.statSync(filePath);
  if (!stat.isFile()) {
    throw new ZipError('not_a_file', `不是文件：${filePath}`);
  }
  if (stat.size > MAX_PACKAGE_BYTES) {
    throw new ZipError('too_large',
      `包过大（${stat.size} 字节 > ${MAX_PACKAGE_BYTES} 上限）`);
  }
  const buf = fs.readFileSync(filePath);
  const entries = readCentralDirectory(buf);
  const byName = new Map(entries.map((e) => [e.name, e]));
  return {
    entries,
    names: entries.map((e) => e.name),
    has(name) {
      return byName.has(name);
    },
    read(name) {
      const entry = byName.get(name);
      if (!entry) {
        throw new ZipError('not_found', `包里没有条目：${name}`);
      }
      return extractEntry(buf, entry);
    },
  };
}

module.exports = {
  open,
  readCentralDirectory,
  extractEntry,
  findEocd,
  ZipError,
  MAX_PACKAGE_BYTES,
};
