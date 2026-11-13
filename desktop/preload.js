'use strict';
/**
 * 渲染端能拿到什么（子任务 5.3 / t12）。
 *
 * 只有这 4 个方法，全部走 ``ipcRenderer.invoke`` 白名单：界面本体（产品单页应用）
 * 完全不需要它们也能跑，桌面壳的信息只用于页脚与"关于"框。
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
});
