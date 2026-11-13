# TraceLens 5.0 · 诚实降级清单（Honest Downgrades）

> 依据：产品纪律「决定 ≠ 生效」「不确定不得说成肯定」「做不到的事不假装做到」。
> 本文件是**发布闸门**的一部分：任何界面文案或 API 字段宣称了下面四项能力之一，即为缺陷。
> 状态：4 项全部为**显式降级**，不是"待实现"。

---

## 0. 判定原则（三条，按顺序适用）

| 顺序 | 原则 | 反例（判为缺陷） |
| --- | --- | --- |
| 1 | 系统只陈述**它真的做过**的事 | 界面写"已恢复文件"（实际只写了本地模拟回执） |
| 2 | 做不到的能力要**说清楚**，并给出可核对的字段 | 只写"上链成功"却不给 `eth_tx_hash` 或 `anchor_state` |
| 3 | 降级必须在**数据结构里留痕**，不能只写在文档里 | 文档说降级，响应里没有任何字段可验证 |

---

## 1. RFC 3161 可信时间戳

**做不到**：RFC 3161 需要外部 TSA 服务（URL + 凭据 + 网络），本项目离线、零网络、零依赖。

**本项目怎么做**：`tsa_timestamp = "local-tsa:<ISO8601Z>"`（[secretariat.py:103](../agent_explainer/secretariat.py#L103) `TSA_LOCAL_PREFIX`），
验签结果里 `tsa_state = "local_only"`。

**界面必须说**：`cards.tsa.local_only` →「本机时间，非外部可信时间戳」。

**禁止说**：「可信时间戳已获取」「时间已由第三方公证」「时间戳有效」。

**自动检测**：响应与 i18n 文案 grep 禁用词表 `可信时间戳`/`trusted timestamp`/`notariz`（命中即失败）。

**要真做需要**：一个可访问的 TSA 服务 URL + 凭据（外部依赖，见 §5）。

---

## 2. TEE / HSM 密钥保管

**做不到**：本机无 TEE，也没有接入 HSM / 系统钥匙串。

**本项目怎么做**：`<data>/features/secretariat/keyring.json`，`chmod 0600`（[secretariat.py:107](../agent_explainer/secretariat.py#L107) `KEYRING_MODE`）。
Windows 上 `chmod` 不生效 → 依赖 NTFS 继承 ACL，**本产品不自动收紧**；`/api/secretariat/status` 用 `keyring_guard`
字段如实标注（`posix_mode` / `unprotected`）。

**界面必须说**：`secretariat.keyring.local_file` →「私钥保存在本机文件，受文件权限保护；未使用硬件密钥库」。

**禁止说**：「硬件级密钥保护」「密钥不可导出」（私钥确实是明文 seed 在文件里）。

**自动检测**：任何 API 响应/日志/审计条目出现 `seed`、`private`、`d=` 片段即失败（[test_secretariat.py](../tests/test_secretariat.py) 已有同类断言，5.0 需扩展到全部 secretariat 路由）。

**要真做需要**：Windows DPAPI/CNG 或硬件令牌接入 + 跨平台测试机。

---

## 3. 「双人审批」的真实身份

**做不到**：离线单机没有第二个人，也没有企业身份系统（SSO/DID 注册局）。

**本项目怎么做**：`critical` 档要 **两个不同 `user` DID** 的本地密钥各签一枚
（同 DID 两把钥**不算**双人），`approval.sealed` 再叠总裁办盖章。

**界面必须说**：`secretariat.approval.local_keys` →「审批签名只证明这次审批在本机被记录并签名，不证明真实世界里的签字意愿」。

**禁止说**：「已获双人授权」「符合双人复核要求」「合规双签」。

**自动检测**：`approval.approvers[].did` 去重后数量必须等于 `approval.required`；文案 grep 禁用词「双人授权」/`dual authoriz`。

**要真做需要**：第二个真人 + 身份提供商（外部依赖）。

---

## 4. 区块链存证（IPFS + 以太坊）

**做不到**：没有 IPFS 节点、没有以太坊 RPC、没有账户与 gas。

**本项目怎么做**：
- CID 用**本地内容寻址**计算（`sha256` → CIDv1，`raw` codec），字段 `ipfs_cid` 照实给出；
- `anchor_state = "unanchored"`；
- `eth_tx_hash = null`，**绝不填造**；
- 导出面板显示 `COMPLIANCE_ANCHOR_UNAVAILABLE` 的 503 语义说明（错误表已备，[server.py:634](../agent_explainer/server.py#L634)）。

**界面必须说**：`compliance.anchor.unanchored` →「已生成内容指纹（CID），**未上链**；无区块链时间证明」。

**禁止说**：「已上链」「链上存证」「不可篡改已公证」「以太坊已确认」。

**自动检测**：导出包 `manifest.json` 中 `eth_tx_hash` 必须为 `null` 且 `anchor_state == "unanchored"`；
全仓 grep `eth_tx_hash` 的非空赋值即失败。

**要真做需要**：IPFS 节点 + 以太坊 RPC + 有余额的账户（外部依赖，且引入隐私与合规风险）。

---

## 5. 需要外部条件才能升级的清单（**唯一需要人介入的部分**）

| # | 能力 | 需要什么 | 不提供时的状态 |
| --- | --- | --- | --- |
| 1 | RFC 3161 时间戳 | TSA 服务 URL + 凭据 | `tsa_state="local_only"` |
| 2 | HSM/TEE 密钥 | 硬件或 DPAPI/CNG 集成 | `keyring_guard="unprotected"`（Windows） |
| 3 | 真实双人审批 | 第二个真人 + 身份源 | 本地双 DID 密钥，文案如实声明 |
| 4 | 区块链存证 | IPFS 节点 + ETH RPC + gas | `anchor_state="unanchored"`、`eth_tx_hash=null` |
| 5 | sidecar exe 构建 | `pip install pyinstaller`（需联网） | e2e 的 sidecar 用例保持 known-red（不阻塞 5.0） |

---

## 6. 建议的守卫测试（新增 `tests/test_honest_copy.py`）

1. 加载 `web/i18n/zh.json` + `en.json`，断言上述禁止短语在所有 `*.error.*` 与 §1–§4 相关键中**零命中**；
2. 起一个 `--mode local` 实例，抓 `/api/secretariat/status`、`/api/compliance/exports`、任意已签记录的 `verify` 结果，
   断言 `tsa_state == "local_only"`、`anchor_state == "unanchored"`、`eth_tx_hash is None`；
3. 断言 `approval.approvers` 中 DID 去重后数量满足 `approval.required`（禁止同 DID 双签冒充双人）；
4. 断言三个已知 BOM 产物不再出现（`SIGNATURE-STATUS.json` 以 `{` 开头）。

> 本文件与 `docs/07 §10` 是同一口径的两处表述：`07` 面向实现者，本文件面向发布闸门与文案。
