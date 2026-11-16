#!/usr/bin/env node
'use strict';
/**
 * 更新通道命令行（无 GUI 时用：CI、打包机、运维脚本）。
 *
 *   node desktop/updater-cli.js status
 *   node desktop/updater-cli.js check   --source D:/tracelens-updates
 *   node desktop/updater-cli.js install --source D:/tracelens-updates [--version 0.6.1] [--allow-downgrade]
 *   node desktop/updater-cli.js rollback [--to 0.6.0]
 *   node desktop/updater-cli.js list
 *
 * `--data <dir>` 指定 userData（默认按平台推断）。**这个 CLI 不进安装包**
 * （electron-builder 的 files 白名单里没有它），它只服务开发与运维。
 *
 * 输出一律是 JSON 单行，便于脚本消费；退出码 0 = 成功，1 = 失败。
 */

const os = require('node:os');
const path = require('node:path');
const { createUpdater } = require('./updater.js');

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const item = argv[i];
    if (item.startsWith('--')) {
      const key = item.slice(2);
      const next = argv[i + 1];
      if (next !== undefined && !next.startsWith('--')) {
        out[key] = next;
        i += 1;
      } else {
        out[key] = true;
      }
    } else {
      out._.push(item);
    }
  }
  return out;
}

function defaultUserData() {
  if (process.platform === 'win32') {
    const appData = process.env.APPDATA || path.join(os.homedir(), 'AppData', 'Roaming');
    return path.join(appData, 'TraceLens 桌面端');
  }
  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'TraceLens 桌面端');
  }
  return path.join(process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config'), 'TraceLens 桌面端');
}

function emit(value, code) {
  // 用 exitCode 而不是 process.exit()：后者会在 Windows 管道下截断还没 flush
  // 的 stdout，调用方拿到半截 JSON（实测踩过一次，`check` 失败时 stdout 为空）。
  process.stdout.write(`${JSON.stringify(value, null, 2)}\n`);
  process.exitCode = code;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const command = args._[0] || 'status';
  const userDataDir = args.data || defaultUserData();

  if (command === 'list') {
    const up = createUpdater({ userDataDir, appVersion: '0.0.0' });
    emit({ userDataDir, versions: up.installedVersions(), state: up.readState() }, 0);
    return;
  }

  const up = createUpdater({
    userDataDir,
    appVersion: args['host-version'] || '0.0.0',
    capabilities: args.runtime ? ['web', 'runtime'] : ['web'],
  });

  if (command === 'status') {
    emit(up.status(), 0);
    return;
  }

  if (command === 'check') {
    const res = await up.check({ source: args.source });
    emit(res, res.ok ? 0 : 1);
    return;
  }

  if (command === 'install') {
    const res = await up.install({
      source: args.source,
      version: typeof args.version === 'string' ? args.version : undefined,
      allowDowngrade: args['allow-downgrade'] === true,
    });
    emit(res, res.ok ? 0 : 1);
    return;
  }

  if (command === 'rollback') {
    const res = up.rollback({ to: typeof args.to === 'string' ? args.to : undefined });
    emit(res, res.ok ? 0 : 1);
    return;
  }

  emit({ ok: false, reason: 'unknown_command', message: `不认识的命令：${command}`, command }, 1);
}

main().catch((err) => {
  emit({ ok: false, reason: 'cli_exception', message: String(err && err.message ? err.message : err) }, 1);
});
