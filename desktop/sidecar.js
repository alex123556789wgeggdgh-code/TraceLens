'use strict';
/**
 * Python sidecar 的拉起与看护（子任务 5.3 / t12）。
 *
 * 两种模式只有一处不同（`resolveLaunch`），其余流程完全一致 —— 这是刻意设计，
 * 免得"开发模式跑得通、打包模式起不来"：
 *
 * * 开发模式（未打包）：`python -m agent_explainer --sidecar --port 0 --data-dir … --features …`
 *   用系统 Python；解释器可用 `TRACELENS_PYTHON` 指定（CI 里常用 `py -3.14`）。
 * * 打包模式：`<resources>/sidecar/agent-explainer-sidecar.exe --sidecar …`
 *   由 `tools/build_sidecar.py`（PyInstaller 单文件）产出，界面资源与演示夹具
 *   已用 `--add-data` 打进 exe。
 *
 * 端口靠 sidecar 打印的机器可读行 `{"tracelens_bootstrap":{…}}` 拿（`--port 0`
 * = 让系统挑空闲端口，避免"端口被占"这种桌面端最常见的启动失败）。
 * 拿到端口后必须等 `GET /health` 真的 200 才允许加载界面 —— 只看进程活着不算。
 */

const { spawn } = require('node:child_process');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

const BOOTSTRAP_KEY = 'tracelens_bootstrap';

/** 开发模式用系统 Python，打包模式用 resources 里的单文件 exe。 */
function resolveLaunch(options) {
  const { packaged, resourcesPath, python, mode, dataDir, demo, port, recordsStore, featuresDir } = options;
  const args = ['--sidecar', '--port', String(port), '--mode', mode,
                '--data-dir', dataDir, '--demo', demo];
  // 总裁办数据目录（身份账本 / 委托 / 认定书 / 密钥库）：`--features` 是 server.py 的选项，
  // 默认 `var/features` —— **与 --data-dir 无关**，冻结态（打包 exe）下解析到安装目录内部
  // `_internal/var/features/secretariat/`。装进 Program Files 后那里不可写 → 登记身份直接
  // 失败；即便可写，升级覆盖安装目录会冲掉身份账本与密钥库 keyring.json（密钥丢失＝已签
  // 记录不可验证），多用户共用一台机器还会互相踩同一份账本。所以必须和 --data-dir 一样
  // 显式指到 userData 之下（实测：传了它账本就落在 userData/features/secretariat/）。
  if (featuresDir) {
    args.push('--features', featuresDir);
  }
  if (recordsStore) {
    args.push('--records-store', recordsStore);
  }
  if (packaged) {
    const exeName = process.platform === 'win32' ? 'agent-explainer-sidecar.exe' : 'agent-explainer-sidecar';
    // 两种 PyInstaller 布局都认：单文件（任务书里的形态）与单目录（官方推荐、
    // 启动更快、不往临时目录解压）。落点由 tools/build_sidecar.py 决定。
    const candidates = [
      path.join(resourcesPath, 'sidecar', exeName),
      path.join(resourcesPath, 'sidecar', 'agent-explainer-sidecar', exeName),
    ];
    const exe = candidates.find((c) => fs.existsSync(c));
    if (!exe) {
      throw new Error(
        `找不到 sidecar：\n  ${candidates.join('\n  ')}\n` +
        '打包前请先跑 python tools/build_sidecar.py --vendor --build --onedir' +
        '（PyInstaller 产物要落进 desktop/build/resources/sidecar/，' +
        'electron-builder 的 extraResources 会把它放进 resources/sidecar/）。');
    }
    return { command: exe, args, label: exeName };
  }
  const interpreter = python
    || process.env.TRACELENS_PYTHON
    || (process.platform === 'win32' ? 'python' : 'python3');
  return { command: interpreter, args: ['-m', 'agent_explainer', ...args],
           label: `${interpreter} -m agent_explainer` };
}

/** 一次 GET /health（只允许回环地址；产品没有外部出网点）。 */
function health(port, timeoutMs) {
  return new Promise((resolve) => {
    const req = http.get(
      { host: '127.0.0.1', port, path: '/health', timeout: timeoutMs || 2000 },
      (res) => {
        let body = '';
        res.setEncoding('utf8');
        res.on('data', (chunk) => { body += chunk; });
        res.on('end', () => {
          if (res.statusCode !== 200) {
            resolve({ ok: false, reason: `HTTP ${res.statusCode}` });
            return;
          }
          try {
            resolve({ ok: true, json: JSON.parse(body) });
          } catch (err) {
            resolve({ ok: false, reason: `健康检查不是 JSON：${err.message}` });
          }
        });
      });
    req.on('error', (err) => resolve({ ok: false, reason: err.message }));
    req.on('timeout', () => {
      req.destroy(new Error('健康检查超时'));
    });
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * 子进程 stdout/stderr 的解码器（5.3 / t3 F2）。
 *
 * 实测口径（docs/audit/v050-desktop.md 的 F2 节）：
 * * 源码态的系统 Python 认 `PYTHONUTF8` / `PYTHONIOENCODING`，注入后输出是 UTF-8；
 * * **PyInstaller 冻结态的 exe 完全不认这两个变量** —— 本机实测 `PYTHONIOENCODING`
 *   取 `ascii` / `utf-8` / `gbk` / 不设，四次的 stdout 字节数与内容一模一样（699B），
 *   永远按系统代码页（本机 cp936/GBK）吐字节。
 * 所以只注入环境变量（`startSidecar` 里那四个）只修好了一半：打包态的日志与
 * bootstrap 行里的中文路径按 UTF-8 硬解会变成 U+FFFD 乱码。这里两条都认：
 * 先按 UTF-8 严格流式解，一出现非法字节就把这条流整体切到 GBK（cp936）重放当前块，
 * 后续块继续流式解。ASCII 部分两种解码结果相同，切换点之前不会重复。
 */
function makeStreamDecoder(log) {
  const utf8 = new TextDecoder('utf-8', { fatal: true });
  const gbk = new TextDecoder('gbk');
  let mode = 'utf-8';
  return (chunk) => {
    const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    if (mode === 'gbk') {
      return gbk.decode(buf, { stream: true });
    }
    try {
      return utf8.decode(buf, { stream: true });
    } catch (err) {
      mode = 'gbk';
      log(`sidecar 输出不是 UTF-8（冻结态 exe 实测不认 PYTHONUTF8），本次输出按 GBK/cp936 解码`);
      return gbk.decode(buf);
    }
  };
}

/**
 * 拉起 sidecar，返回 { proc, bootstrap, health, logs, stop, onExit }。
 * 失败时 reject 一句人话（带最后几行输出，方便照着排障）。
 */
async function startSidecar(options) {
  const log = options.log || (() => {});
  const launch = resolveLaunch(options);
  // 逐行刷新 + 强制 UTF-8 stdio：管道是块缓冲，bootstrap 行会卡在缓冲区里读不到。
  // 注意：注入 PYTHONUTF8/PYTHONIOENCODING 只对**源码态**的系统 Python 有效，
  // 冻结态 exe 实测不认（见 `makeStreamDecoder` 的注释），所以打包态靠解码器兜底。
  const env = Object.assign({}, process.env, {
    TRACELNS_SIDECAR: '1',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
    PYTHONUNBUFFERED: '1',
  });
  log(`启动 sidecar：${launch.label}`);
  const proc = spawn(launch.command, launch.args, {
    cwd: options.cwd,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  });
  const logs = [];
  const tail = () => logs.slice(-25).join('\n');
  const push = (chunk) => {
    String(chunk).split(/\r?\n/).forEach((line) => {
      if (line.trim()) {
        logs.push(line);
        log(line);
      }
    });
  };
  const decodeOut = makeStreamDecoder(log);
  const decodeErr = makeStreamDecoder(log);
  proc.stdout.on('data', (chunk) => push(decodeOut(chunk)));
  proc.stderr.on('data', (chunk) => push(decodeErr(chunk)));

  const exitEvents = [];
  const exitPromise = new Promise((resolve) => {
    proc.on('exit', (code, signal) => {
      exitEvents.push({ code, signal });
      resolve({ code, signal });
    });
    proc.on('error', (err) => {
      exitEvents.push({ error: err.message });
      resolve({ code: -1, signal: null, error: err.message });
    });
  });
  // 启动失败最常见的原因是没装 Python：spawn 直接 ENOENT，必须翻成人话
  const first = await Promise.race([
    exitPromise,
    sleep(1500).then(() => null),
  ]);
  if (first && first.error) {
    throw new Error(
      `拉不起来：${first.error}\n` +
      (options.packaged
        ? '先跑 python tools/build_sidecar.py 产出 sidecar exe。'
        : `开发模式需要系统 Python（当前用 ${launch.command}），可用 TRACELENS_PYTHON 指定解释器。`)
      + `\n最后几行输出：\n${tail()}`);
  }

  const bootstrap = await waitForBootstrap(proc, options.bootTimeout || 60000, tail, logs);
  if (!bootstrap) {
    const info = await exitPromise;
    throw new Error(
      `60 秒内没读到 ${BOOTSTRAP_KEY} 行（退出码 ${info.code}，信号 ${info.signal}）。\n`
      + `最后几行输出：\n${tail()}`);
  }

  const deadline = Date.now() + (options.healthTimeout || 45000);
  let lastReason = '未开始';
  while (Date.now() < deadline) {
    const check = await health(bootstrap.port, 2000);
    if (check.ok) {
      log(`sidecar 就绪：${bootstrap.url}（审计链 ${check.json.audit && check.json.audit.ok ? 'ok' : '见 /health'}）`);
      return {
        proc,
        bootstrap,
        healthJson: check.json,
        logs,
        stop() {
          if (proc.exitCode === null && proc.signalCode === null) {
            proc.kill();
          }
        },
        onExit(handler) {
          exitPromise.then(handler);
        },
      };
    }
    lastReason = check.reason;
    await sleep(300);
  }
  proc.kill();
  throw new Error(`sidecar 起来了但 /health 一直没就绪（端口 ${bootstrap.port}，`
    + `最后原因：${lastReason}）。\n最后几行输出：\n${tail()}`);
}

/** 从 stdout 里找那一行机器可读的 bootstrap JSON。 */
function waitForBootstrap(proc, timeoutMs, tail, logs) {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    const tick = () => {
      for (const line of logs) {
        const text = line.trim();
        if (text.startsWith('{') && text.includes(BOOTSTRAP_KEY)) {
          try {
            resolve(JSON.parse(text)[BOOTSTRAP_KEY]);
            return;
          } catch (err) {
            resolve(null);
            return;
          }
        }
      }
      if (Date.now() > deadline || proc.exitCode !== null) {
        resolve(null);
        return;
      }
      setTimeout(tick, 50);
    };
    tick();
  });
}

module.exports = {
  startSidecar, resolveLaunch, health, makeStreamDecoder, BOOTSTRAP_KEY,
};
