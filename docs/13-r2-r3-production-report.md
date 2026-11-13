# 总裁办 R2 · R3 制作报告（后端工程师）

> 制作日期：2026-11-11 · 规格依据：[docs/12](12-secretariat-delta-spec.md)（R1 冻结稿）
> 对应角色：**后端工程师**（原分工 `strata swift 1.5 iq3-s`）

## 0. 模型分工与本次实际执行

| 角色 | 原分工模型 | 本会话是否注册 | 实际执行 |
| --- | --- | --- | --- |
| 产品经理 / 架构师（R1） | `strata swift 1.5 iq3-s` | ❌ 未注册 | 由 Lead 以该角色口径完成（R1 规格已交付） |
| 后端工程师（R2/R3） | `strata swift 1.5 iq3-s` | ❌ 未注册 | 由 Lead 以「后端工程师」口径执行，证据见下 |
| 全栈（R4） | 未指定 | — | 由 Lead 执行，见 [docs/14](14-r4-production-report.md) |
| 测试与质量官（验收） | `DeepSeek-V4.1-Flash (1017)` | ✅ `deepseek-official/deepseek-flash` | **独立 subagent** 执行，见 [docs/15](15-qa-r2r4-acceptance.md) |

**模型路由事实（不回避）**：本会话 `list_subagent_models` 只注册了
`deepseek-official/deepseek-flash`（DeepSeek-V4.1-Flash）。`strata swift 1.5 iq3-s`
在本机没有 provider 路由，无法把 R2/R3 真的交给该模型；因此代码制作由 Lead 代行，
**测试与质量官按原分工在 DeepSeek-V4.1-Flash 上独立执行**——这是本会话唯一能落地的模型分工。

---

## 1. R2：身份确权与责任链（缺口闭合）

**目标**：身份不可伪造、吊销即失效、责任链里的每一段都是 DID 且不可由入参替别人派责任。

| # | 变更 | 位置 | 为什么 |
| --- | --- | --- | --- |
| 1 | `agent_id` 形状校验（`^[A-Za-z0-9._:-]{1,120}$`）+ Windows 保留名拒绝 | `secretariat.py::register_identity` | docs/09 D13：它会变成 keyring 键与文件名 |
| 2 | `owner_did` 必须是合法 DID（`$defs/did` 同形） | 同上 + `_require_record_did` | 责任链的根不能是随便一个字符串 |
| 3 | 登记幂等：同 id/同所属人/同镜像/同启动参数 → 返回原记录，不追加账本行 | 同上 | 原实现重复登记会追加新行（docstring 说幂等、代码没做到） |
| 4 | 换实例必须显式 `rotate=True`；已吊销身份不静默复活 | 同上 | 避免"重登记顺手复活"绕过吊销 |
| 5 | 吊销幂等且终局：已吊销再吊销返回当前记录，不再追加 | `revoke_identity` | 同一件事在账本里出现两次吊销，时间线会被读成吊销了两次 |
| 6 | 委托双方必须是 DID；Agent 委托人必须有出处（已登记实例或某条委托的受托人） | `create_delegation` + `_known_agent_did` | 责任链不能凭空多出一段 |
| 7 | 责任链链尾改为 **实例 DID**（`did_from_pubkey(pub, kind="agent")`），不再是裸 `agent_id` | `sign_record` + `agent_did_of` | schema 要求每项都是 DID；实测 `demo-photo-bot` 直接报 pattern |

**验收**（[docs/12 §7](12-secretariat-delta-spec.md) A3–A5、A9）

- 身份登记 / 轮换 / 吊销只追加：账本 `verify()["ok"]`，重复登记只 1 行；
- 私钥零泄漏：身份/状态响应序列化后不含 `seed`/`private_key`；
- 吊销后签名 → 400 `SECRETARIAT_IDENTITY_REVOKED`；
- 伪造 `human_owner` / 链根 / 链尾 / 非法 DID → 400；
- 委托只能收窄、深度 ≤5、过期失效（既有 40 条测试 + 新增契约测试覆盖）。

---

## 2. R3：行为签名与哈希链（契约合规加固）

**目标**：签名记录能过自家 schema；审批签"真验、绑定记录、双人不同人"；认定书可离线复算。

| # | 变更 | 位置 | 为什么 |
| --- | --- | --- | --- |
| 1 | `action_record.schema.json` 增补可选 `tool`（string ≤120） | schema + docs/02 §2.8 + docs/07 §3.1 | 签名域要覆盖工具名；schema `additionalProperties:false` 下原实现必然违约 |
| 2 | `approval` 收敛到 docs/02 形状：`{required, approvers[{did,role,signed_at,signature}], sealed, seal?}` | `_build_approval` | 原实现多写 `policy`/`local_only`/`fingerprint`/`risk_level`/`required`，实测 5 条 schema 错误 |
| 3 | 审批签 payload 绑定记录：`{did, role, signed_at, record_id, action_type, input_hash, output_hash, risk_level, required}` | 同上 | 原 payload 不含记录绑定，实测同一人同一时钟两条记录签名逐字节相同（可跨记录复用） |
| 4 | 同一 DID 只算一个人（critical 必须两个不同 user DID） | 同上 | docs/09 裁决 D8；原实现传两次同 DID 即通过 |
| 5 | `verify_record` 真实验签审批签；验不过 → `SECRETARIAT_SIGNATURE_INVALID`（scope=approval） | `_verify_approvers` | 原实现只数个数，不验签 |
| 6 | critical 自动盖章并校验：`approval.seal.digest == approval_digest(record)`、`verify_seal` | `_build_approval` + `verify_record` | 让"盖章"成为可验证事实，而不只是一个布尔 |
| 7 | `risk_level` 签名前归一化并写回 | `sign_record` | 避免"签名时说 high、记录里还是 low" |
| 8 | `human_owner`/`delegation_chain` 改为校验（不再 `setdefault`） | `sign_record` + `_validated_chain` | docs/09 B0-2：导入方可替别人派责任 |
| 9 | 认定书 `actions[]` 内嵌离线复算最小字段集；`records_root` 改用**动作条目**复算 | `build_letter`、`verify_letter` | 原实现构建用全量记录、复核用动作子集 → 根哈希自相矛盾（实测必现） |

**验收**（[docs/12 §7](12-secretariat-delta-spec.md) A1–A2、A6–A8、A10–A11）

- 11 个签名域逐字段篡改 → `verify_record().ok=False`；
- low/medium/high/critical 四档签名后 **schema 0 错误**；
- 审批签跨记录不同；伪造审批签进 `problems`；同 DID 双人 400；
- 认定书离线复核：认定书签名 + `records_root` + 逐条动作签名；改一个签名域字段 → 退出码 1。

---

## 3. 复跑命令与实测结果

```powershell
cd agent-explainer
$env:TMP="$PWD\.tmp"; $env:TEMP=$env:TMP; $env:PYTHONPATH="$PWD\.tmp\pyfix"

python -m unittest tests.test_secretariat -q                 # 40/40 OK
python -m unittest tests.test_secretariat_contract -q        # 12/12 OK
python -m unittest tests.test_secretariat_autosign -q        # 4/4 OK（R4 集成）
```

| 断言 | 实测 |
| --- | --- |
| 四档签名记录过 schema | 0 errors（R3 修复前：8 errors / 6 条由签名器造成） |
| 11 域篡改检出 | 11/11 |
| 审批签跨记录复用 | 修复前 True → 修复后 False |
| 同 DID 双人审批 | 修复前通过 → 修复后 400 `SECRETARIAT_FIELD_INVALID`（field=approvers）；完全没传审批人才是 409 `APPROVAL_REQUIRED` |
| 责任链链尾 | 修复前裸 `agent_id`（pattern 报错）→ 修复后实例 DID |
| 认定书离线复核 | PASS；篡改 `timestamp` → FAIL（exit 1） |

---

## 4. 残余风险（R2/R3 范围内）

1. 委托记录的 DID 校验是"四段结构"而不是 schema 的严格 pattern（历史委托兼容），
   写进**已签名记录**的 DID 才用严格 pattern——两者口径已在 docs/12 §3 写明。
2. 审批签的"真实世界签字意愿"仍不成立（本地 user 密钥），界面与 docs/12 §10 已如实标注。
3. 认定书的审批签与身份绑定无法离线复算（需身份账本片段），属 docs/12 §5 冻结边界。
