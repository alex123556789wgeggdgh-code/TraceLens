'use strict';
/**
 * 渲染端能拿到什么（子任务 5.3 / t12；0.6.0 扩更新通道）。
 *
 * 只有两族能力，全部走 ``ipcRenderer.invoke`` 白名单：
 *
 *  * 基础五个（info / status / logs / quit / platform）—— 界面本体不需要它们也能跑，
 *    桌面壳的信息只用于页脚与"关于"框。
 *  * ``update`` 一族（0.6.0）—— 检查/安装/回滚/改配置/重启。
 *    **不能指定更新源**：源只认 `config.json` 里用户自己写的地址，渲染端传进来
 *    的 source 会在主进程被丢掉（见 main.js 的 UPDATE_CONFIG_KEYS 与 install 白名单）。
 *    否则任何能执行 JS 的页面都能把应用指向任意地址下载代码，等于开后门。
 *
 * 不递刀子：渲染端拿不到文件、网络、子进程能力，`sandbox: true` +
 * `contextIsolation: true` 之下本来就没有 Node 能力，这里也不额外开口子。
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('tracelensDesktop', {
  info: () => ipcRenderer.invoke('tracelens:info'),
  status: () => ipcRenderer.invoke('tracelens:status'),
  logs: () => ipcRenderer.invoke('tracelens:logs'),
  quit: () => ipcRenderer.invoke('tracelens:quit'),
  platform: process.platform,

  update: {
    status: () => ipcRenderer.invoke('tracelens:update-status'),
    check: () => ipcRenderer.invoke('tracelens:update-check'),
    install: (options) => ipcRenderer.invoke('tracelens:update-install', options || {}),
    config: (patch) => ipcRenderer.invoke('tracelens:update-config', patch || {}),
    rollback: (options) => ipcRenderer.invoke('tracelens:update-rollback', options || {}),
    restart: () => ipcRenderer.invoke('tracelens:update-restart'),
    /** 菜单「检查更新」触发的单向通知（渲染端据此打开更新面板）。 */
    onOpenRequest: (callback) => {
      if (typeof callback === 'function') {
        ipcRenderer.on('tracelens:open-update', () => callback());
      }
    },
  },
});
