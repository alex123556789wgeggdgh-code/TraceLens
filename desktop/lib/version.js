'use strict';
/**
 * 语义化版本比较（更新通道用）。
 *
 * 为什么不用 `localeCompare` / 字符串比较：`0.10.0` 会被判成小于 `0.9.0`，
 * 后果是「新版本永远装不上」，而且是那种上线三个月后才发现的问题。
 *
 * 支持：`1.2.3`、`v1.2.3`、`1.2.3-beta.1`、`1.2`（缺省补 0）。
 * 预发布排序按 semver 2.0.0：`1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0`。
 */

const VERSION_RE = /^v?(\d+)\.(\d+)(?:\.(\d+))?(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/;

/** 解析成 {major, minor, patch, pre[]}；不合法返回 null（调用方必须处理）。 */
function parse(version) {
  if (typeof version !== 'string') {
    return null;
  }
  const m = VERSION_RE.exec(version.trim());
  if (!m) {
    return null;
  }
  return {
    major: Number(m[1]),
    minor: Number(m[2]),
    patch: m[3] === undefined ? 0 : Number(m[3]),
    pre: m[4] ? m[4].split('.') : [],
  };
}

function comparePre(a, b) {
  // 无预发布标识 > 有预发布标识（1.0.0 > 1.0.0-rc.1）
  if (a.length === 0 && b.length === 0) {
    return 0;
  }
  if (a.length === 0) {
    return 1;
  }
  if (b.length === 0) {
    return -1;
  }
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i += 1) {
    const x = a[i];
    const y = b[i];
    const xn = /^\d+$/.test(x);
    const yn = /^\d+$/.test(y);
    if (xn && yn) {
      const d = Number(x) - Number(y);
      if (d !== 0) {
        return d > 0 ? 1 : -1;
      }
    } else if (xn !== yn) {
      return xn ? -1 : 1; // 数字标识 < 字母标识
    } else if (x !== y) {
      return x > y ? 1 : -1;
    }
  }
  if (a.length === b.length) {
    return 0;
  }
  return a.length > b.length ? 1 : -1;
}

/** 返回 -1 / 0 / 1；任一侧非法抛 TypeError（不做「猜一个」的容错）。 */
function compare(a, b) {
  const pa = parse(a);
  const pb = parse(b);
  if (!pa || !pb) {
    throw new TypeError(`版本号不合法：${!pa ? a : b}`);
  }
  for (const key of ['major', 'minor', 'patch']) {
    if (pa[key] !== pb[key]) {
      return pa[key] > pb[key] ? 1 : -1;
    }
  }
  return comparePre(pa.pre, pb.pre);
}

/** a >= b */
function gte(a, b) {
  return compare(a, b) >= 0;
}

/** a > b */
function gt(a, b) {
  return compare(a, b) > 0;
}

/** 校验格式，给 manifest / index 校验用；返回 true/false 不抛。 */
function isValid(version) {
  return parse(version) !== null;
}

module.exports = { parse, compare, gte, gt, isValid };
