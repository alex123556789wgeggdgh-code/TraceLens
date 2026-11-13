'use strict';
/**
 * TraceLens 桌面壳主进程（子任务 5.3 / t12）。
 *
 * 流程（与 `desktop/README.md` 一字不差）：
 *
 *   单实例锁 → 拉起 Python sidecar（开发=系统 Python / 打包=resources 里的 exe）
 *   → 读 bootstrap 行拿端口 → 轮询 GET /health 到 200 → 创建窗口加载 http://127.0.0.1:<port>/
 *   → 退出时 terminate 子进程
 *
 * 界面本体是产品自带的单页应用（`agent_explainer/web/`），桌面壳**不复制**任何 UI，
 * 所以不存在"桌面端和网页端长得不一样"的分裂；语言、数据、干预、审计全走同一套 API。
 *
 * 安全口径：`contextIsolation: true` / `nodeIntegration: false` / `sandbox: true`；
 * 只加载回环地址；preload 只暴露 4 个白名单 IPC；不调用 `shell.openExternal`
 * （产品是离线优先，不替用户开外链）。
 *
 * `--selfcheck`：无人值守验收用的模式 —— 启动、加载 UI、读页面标题与卡片数、
 * 打印一行 `{"tracelens_desktop_selfcheck":{…}}`、按结果以 0/1 退出。
 * CI 与"这台机器到底能不能跑"的验收都用它，不需要人手点窗口。
 */

const { app, BrowserWindow, Menu, ipcMain, dialog } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const http = require('node:http');
const { startSidecar } = require('./sidecar.js');

const SELF_CHECK = process.argv.includes('--selfcheck')
  || process.env.TRACELENS_SELFCHECK === '1';
const SELF_CHECK_KEY = 'tracelens_desktop_selfcheck';

/**
 * 命令行开关（优先级高于环境变量）：从桌面 Shell 直接拉起进程时传不进环境变量，
 * 验收就只能靠这几个开关指定输出文件与数据目录。
 * `--selfcheck-out <文件>`：把验收 JSON 追加写进文件（stdout 拿不到时的兜底）。
 */
function flag(name) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--')
    ? process.argv[i + 1] : null;
}

const FLAGS = {
  selfCheckOut: flag('selfcheck-out'),
  dataDir: flag('data-dir'),
  featuresDir: flag('features-dir'),
  python: flag('python'),
  mode: flag('mode'),
  demo: flag('demo'),
  port: flag('port'),
};

/** 用户数据目录：打包模式落 userData（升级安装不丢记录），开发模式可用环境变量指到仓库 var/。 */
function dataDir() {
  if (FLAGS.dataDir) {
    return FLAGS.dataDir;
  }
  if (process.env.TRACELENS_DATA_DIR) {
    return process.env.TRACELENS_DATA_DIR;
  }
  return path.join(app.getPath('userData'), 'data');
}

/**
 * 总裁办数据目录（身份账本 / 委托 / 认定书 / 密钥库 keyring.json）。
 *
 * 口径与 `dataDir()` 完全并列，**不能省**：服务端 `--features` 默认 `var/features`，
 * 打包态下那就是安装目录内部（Program Files 不可写、升级会冲掉密钥库）。所以和
 * `--data-dir` 一样指到 userData 之下，升级/卸载重装都留在用户目录里。
 */
function featuresDir() {
  if (FLAGS.featuresDir) {
    return FLAGS.featuresDir;
  }
  if (process.env.TRACELENS_FEATURES_DIR) {
    return process.env.TRACELENS_FEATURES_DIR;
  }
  return path.join(app.getPath('userData'), 'features');
}

const state = {
  win: null,
  sidecar: null,
  quitting: false,
  starting: true,
  lastError: null,
  logs: [],
};

function log(line) {
  state.logs.push(String(line));
  if (state.logs.length > 500) {
    state.logs.splice(0, state.logs.length - 500);
  }
  process.stdout.write(`[desktop] ${line}\n`);
}

function ensureDataDir() {
  const dir = dataDir();
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function buildMenu() {
  const template = [
    {
      label: '文件',
      submenu: [{ label: '重新加载', accelerator: 'CmdOr+R', role: 'reload' },
        { label: '开发者工具', role: 'toggleDevTools' },
        { type: 'separator' },
        { label: '退出', role: 'quit' }],
    },
    {
      label: '帮助',
      submenu: [{
        label: `关于 TraceLens ${app.getVersion()}`,
        click() {
          const info = state.sidecar ? state.sidecar.bootstrap : null;
          dialog.showMessageBoxSync({
            type: 'info',
            title: '关于 TraceLens',
            message: `TraceLens 桌面端 ${app.getVersion()}`,
            detail: info
              ? `产品服务 ${info.version} · ${info.mode} 模式 · 端口 ${info.port}\n`
                + `数据目录 ${info.data_dir}\n界面 http://127.0.0.1:${info.port}/`
              : '产品服务还没起来',
          });
        },
      }],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 380,
    minHeight: 520,
    show: false,
    backgroundColor: '#f6f7f9',
    title: 'TraceLens',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });
  state.win = win;
  win.once('ready-to-show', () => win.show());
  win.on('closed', () => {
    state.win = null;
  });
  // 窗口里的导航只允许留在回环地址，其它一律拦下（离线优先，不替用户开外链）
  win.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith('http://127.0.0.1:')) {
      event.preventDefault();
      log(`拦下越界导航：${url}`);
    }
  });
  return win;
}

function fetchHealth(port) {
  return new Promise((resolve) => {
    const req = http.get({ host: '127.0.0.1', port, path: '/health', timeout: 2000 }, (res) => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', (chunk) => {
        body += chunk;
      });
      res.on('end', () => {
        try {
          resolve({ ok: res.statusCode === 200, json: JSON.parse(body) });
        } catch (err) {
          resolve({ ok: false, reason: String(err.message) });
        }
      });
    });
    req.on('error', (err) => resolve({ ok: false, reason: err.message }));
    req.on('timeout', () => req.destroy(new Error('超时')));
  });
}

/** 验收探针：读页面标题、卡片数、语言，确认"UI 真的加载出来了"。 */
async function probePage(win) {
  return win.webContents.executeJavaScript(`(function () {
    var cards = document.querySelectorAll('.record-card, .card, [data-record-id]').length;
    var lang = (document.documentElement.getAttribute('lang') || '');
    var hasApp = !!window.TraceLensUI;
    var bodyChars = (document.body && document.body.innerText || '').length;
    return { title: document.title, cards: cards, lang: lang, hasUI: hasApp, bodyChars: bodyChars };
  })();`);
}

async function runSelfCheck(win, started) {
  let page = null;
  let pageError = null;
  try {
    page = await probePage(win);
  } catch (err) {
    pageError = String(err && err.message ? err.message : err);
  }
  const health = await fetchHealth(started.bootstrap.port);
  const records = health.ok && health.json.records ? health.json.records : null;
  const ok = Boolean(page && page.hasUI && page.cards > 0 && health.ok);
  const report = {
    ok: ok,
    window: { title: page ? page.title : null, cards: page ? page.cards : 0,
      lang: page ? page.lang : null, bodyChars: page ? page.bodyChars : 0,
      pageError: pageError },
    sidecar: started.bootstrap,
    health: health.ok
      ? { status: health.json.status, records: records, audit: health.json.audit,
        records_store: health.json.records_store }
      : { ok: false, reason: health.reason },
    ms: Math.round(performance.now() - started.at),
    self_check_key: SELF_CHECK_KEY,
  };
  process.stdout.write(`${JSON.stringify({ [SELF_CHECK_KEY]: report })}\n`);
  if (FLAGS.selfCheckOut) {
    // 从桌面 Shell 拉起时 stdout 拿不到：把同一行 JSON 追加写进文件，
    // 验收看文件内容，不看"我以为打了"。
    try {
      fs.appendFileSync(FLAGS.selfCheckOut,
        `${JSON.stringify({ [SELF_CHECK_KEY]: report })}\n`, 'utf8');
    } catch (err) {
      process.stderr.write(`[desktop] 写 --selfcheck-out 失败：${err}\n`);
    }
  }
  state.quitting = true;
  if (state.sidecar) {
    state.sidecar.stop();
  }
  app.exit(ok ? 0 : 1);
}

async function boot() {
  const dir = ensureDataDir();
  const started = { at: performance.now() };
  try {
    const sidecar = await startSidecar({
      packaged: app.isPackaged,
      resourcesPath: process.resourcesPath,
      cwd: path.join(__dirname, '..'),
      dataDir: dir,
      featuresDir: featuresDir(),
      mode: FLAGS.mode || process.env.TRACELENS_MODE || 'local',
      demo: FLAGS.demo || process.env.TRACELENS_DEMO || 'zh',
      port: (FLAGS.port || process.env.TRACELENS_PORT)
        ? Number(FLAGS.port || process.env.TRACELENS_PORT) : 0,
      python: FLAGS.python || process.env.TRACELENS_PYTHON,
      log: log,
    });
    state.sidecar = sidecar;
    state.starting = false;
    sidecar.onExit((info) => {
      state.sidecar = null;
      if (!state.quitting) {
        state.lastError = `产品服务退出（退出码 ${info.code}，信号 ${info.signal}）`;
        log(state.lastError);
        if (SELF_CHECK) {
          process.stdout.write(`${JSON.stringify({
            [SELF_CHECK_KEY]: { ok: false, reason: state.lastError, logs: state.logs.slice(-25) },
          })}\n`);
          app.exit(1);
          return;
        }
        dialog.showErrorBox('TraceLens 起不来', `${state.lastError}\n\n`
          + '最后几行输出：\n' + state.logs.slice(-15).join('\n'));
        app.quit();
      }
    });
    const win = createWindow();
    buildMenu();
    win.webContents.once('did-finish-load', () => {
      log(`界面已加载：${sidecar.bootstrap.url}`);
      if (SELF_CHECK) {
        runSelfCheck(win, started);
      }
    });
    win.webContents.once('did-fail-load', (_event, code, description) => {
      state.lastError = `界面加载失败：${code} ${description}`;
      log(state.lastError);
      if (SELF_CHECK) {
        process.stdout.write(`${JSON.stringify({
          [SELF_CHECK_KEY]: { ok: false, reason: state.lastError, logs: state.logs.slice(-25) },
        })}\n`);
        app.exit(1);
      }
    });
    await win.loadURL(sidecar.bootstrap.url);
  } catch (err) {
    const reason = String(err && err.message ? err.message : err);
    state.lastError = reason;
    state.starting = false;
    log(`启动失败：${reason}`);
    if (SELF_CHECK) {
      process.stdout.write(`${JSON.stringify({
        [SELF_CHECK_KEY]: { ok: false, reason: reason, logs: state.logs.slice(-25) },
      })}\n`);
      app.exit(1);
      return;
    }
    dialog.showErrorBox('TraceLens 起不来', reason);
    app.quit();
  }
}

// -- IPC：preload 的白名单，只暴露这 4 个 ---------------------------------- #
ipcMain.handle('tracelens:info', () => ({
  appVersion: app.getVersion(),
  electron: process.versions.electron,
  node: process.versions.node,
  platform: process.platform,
  packaged: app.isPackaged,
  dataDir: dataDir(),
  featuresDir: featuresDir(),
  starting: state.starting,
  lastError: state.lastError,
  bootstrap: state.sidecar ? state.sidecar.bootstrap : null,
}));
ipcMain.handle('tracelens:status', async () => {
  if (!state.sidecar) {
    return { ok: false, reason: state.lastError || '产品服务未运行' };
  }
  return fetchHealth(state.sidecar.bootstrap.port);
});
ipcMain.handle('tracelens:logs', () => state.logs.slice(-200));
ipcMain.handle('tracelens:quit', () => {
  state.quitting = true;
  app.quit();
  return true;
});

// -- 生命周期 -------------------------------------------------------------- #
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  // 已有实例：把它的窗口唤到前台，本实例立刻退出（不重复起 sidecar、不抢端口）
  app.quit();
} else {
  app.on('second-instance', () => {
    if (state.win) {
      if (state.win.isMinimized()) {
        state.win.restore();
      }
      state.win.focus();
    }
  });

  app.whenReady().then(() => {
    buildMenu();
    boot();
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0 && !state.starting) {
        boot();
      }
    });
  });

  app.on('window-all-closed', () => {
    state.quitting = true;
    app.quit();
  });

  app.on('will-quit', () => {
    // 退出清理：记录日志与审计链都是"追加一行 + fsync"，terminate 不丢数据
    if (state.sidecar) {
      log('退出：终止 sidecar');
      state.sidecar.stop();
      state.sidecar = null;
    }
  });
}
