# R2 独立复核：身份确权与责任链（后端工程师口径）

> 复核日期：2026-11-12 · 规格依据：[docs/12 §3/§7](../12-secretariat-delta-spec.md)（冻结表 C / A3–A5、A9）+
> [docs/13 §1](../13-r2-r3-production-report.md)（R2 制作报告）+ [docs/audit/r0-spec-conformance.md](r0-spec-conformance.md)（B04/B05/B06/B07/B08）
> 写作用域：`agent_explainer/secretariat.py`、`tests/test_secretariat*.py`、本报告。**未动** `server.py` / `web` / `docs/schemas`。
> 复核对象：`agent_explainer/secretariat.py`（1663 行，总裁办身份/委托/签名/认定书）

## 0. 结论

**有条件 PASS（可放行 R3/R4 继续，条件是本批次收口"状态码契约"）。**

- **身份确权与责任链的核心不变量成立**：身份只追加 + 幂等 + 轮换 `supersedes` + 吊销终局；
  责任链根=账本 `owner_did`、尾=`did_from_pubkey(pub, kind="agent")`、长度 2–8、每段过严格 DID pattern、
  入参伪造（`human_owner` / `delegation_chain`）一律拒绝；委托只能收窄、深度 ≤5、过期失效。
  这六项都有测试与探针实测支撑（§1、§3）。
- **本轮修掉 6 项 R2 缺口**（§4）：自委托、已吊销实例参与委托、过期父委托续期、`ttl_seconds`
  坏类型冒 500、链根错误码与冻结规格不符（B07）、委托签名"替委托人现造钥匙自证"。
  全部落在 docs/12 §3 既有错误码族内（`DELEGATION_INVALID` / `FIELD_INVALID`），**不新增错误码**。
- **放行条件 = 状态码契约**（§5）：`SCOPE_WIDENED` / `DEPTH_EXCEEDED`（B04）、`IDENTITY_REVOKED`（B06）
  运行时 409，规格与 `EXPANSION_ERROR_TABLE` 写 400。按 lead 指示**未改运行时**，建议改规格。
  `B05` 已按 Q5 冻结口径改成 400（§4 修复 1）。
- **B08 未修**（§7）：修它要定义"user 登记"语义，会打断 `tests/test_secretariat_api.py` 的 T4 根委托流程
  （该文件不在本轮写作用域）→ 列为残余风险并给出方案，交 lead 定夺。

测试事实：总裁办四套 **49 + 13 + 7 + 4 = 73 条全绿**（R2 前 40+12+7+4=63，本轮 +9/+1）；
全量 **929 条 = 1 条已知红**（`test_e2e.ArtifactTests.test_signature_status_is_honest`，与 R2 无关，r0 已登记根因）。

---

## 1. 逐项核对表

判定口径：**成立** = 规格与运行时一致且有测试；**成立(偏状态码)** = 行为对、HTTP 码与文档不一致；
**已修** = 本轮在写作用域内闭合并补测试。行号是本轮改动后的 `secretariat.py`。

### 1.1 `register_identity`（[:469-550](../../agent_explainer/secretariat.py#L469-L550)）

| # | 核对项 | 规格锚点 | 运行时事实（实测） | 判定 |
| --- | --- | --- | --- | --- |
| I1 | `agent_id` 形状 | docs/09 D13 / docs/13 §1 #1 | `AGENT_ID_RE ^[A-Za-z0-9._:-]{1,120}$` + Windows 保留名拒（[:483](../../agent_explainer/secretariat.py#L483)）→ `FIELD_INVALID`/400 | 成立（api 测试 `bad agent`→400 实测） |
| I2 | `owner_did` 必须是严格 DID | docs/12:113（`$defs/did`） | `_require_record_did`（[:488](../../agent_explainer/secretariat.py#L488)，[:127](../../agent_explainer/secretariat.py#L127)）；`did:x:y:z`→400 | 成立（contract `test_owner_did_must_match_schema_shape`） |
| I3 | 登记幂等（不追加账本行） | docs/13 §1 #3 | 同 id/owner/image/boot/kind → 返回原记录，账本仍 1 行 | 成立（contract `test_registration_is_idempotent_and_revocation_is_terminal`） |
| I4 | 信息不同必须显式 `rotate=True` | docs/13 §1 #4 | 落到 `field=rotate` 的 `FIELD_INVALID`/400（[:523](../../agent_explainer/secretariat.py#L523)） | 成立 |
| I5 | 已吊销身份不静默复活 | docs/13 §1 #4 | 同信息重登记被拒；本轮把"谎报信息不同"改为如实报"已被吊销"（[:513-522](../../agent_explainer/secretariat.py#L513-L522)），码/状态仍 `FIELD_INVALID`/400（T2 专属码只有这一个，docs/12:199） | 已修（`test_re_register_after_revoke_says_revoked`） |
| I6 | 轮换保留旧公钥（旧签名仍可验） | docs/13 §1 表 | `supersedes=previous.entry_hash`；`_pubkeys_of` 收历史公钥 → `verify_record` 判 `historical` | 成立（`test_rotation_appends_and_supersedes`） |
| I7 | 私钥零泄漏（A4） | docs/12:237 | `public_identity` 剥 `seed/private_key/secret/privkey`（[:602](../../agent_explainer/secretariat.py#L602)）；`status()["keyring"]` 只给 facts；`keyring.json` 只存 seed+pub | 成立（`test_keyring_file_never_holds_public_material_in_api_shape`、contract `test_identity_records_never_leak_private_material`） |
| I8 | 身份账本 append-only 连续 | A3 | `Ledger.verify()["ok"]`，账本行 4 条全连 | 成立（`test_identities_ledger_chain_is_continuous`） |

### 1.2 `revoke_identity`（[:572-599](../../agent_explainer/secretariat.py#L572-L599)）

| # | 核对项 | 规格锚点 | 运行时事实（实测） | 判定 |
| --- | --- | --- | --- | --- |
| R1 | 吊销=追加，不删历史 | A3 | 追加带 `revoked_at` 的新行，`supersedes` 指向被吊销行；历史公钥仍在账本 | 成立（`test_revoke_appends_and_marks`） |
| R2 | 吊销幂等（不出现两次吊销） | docs/13 §1 #5 | 再吊销返回当前记录，账本里 `revoked_at` 行仍 1 条 | 成立（contract 断言 `len(records)==1`） |
| R3 | 吊销后签名被拒（终局） | A3 | `sign_record` 先查 `identity.revoked_at`（[:650-653](../../agent_explainer/secretariat.py#L650-L653)）→ `SECRETARIAT_IDENTITY_REVOKED` | 成立(偏状态码：运行时 **409**，docs/12:236 写 400 = B06) |
| R4 | 未知 agent 吊销 | docs/12:200（T3 专属码） | `SECRETARIAT_IDENTITY_NOT_FOUND`/404 | 成立（`test_revoke_unknown_identity` + api T3 200/404 实测） |
| R5 | 吊销是否阻断委托派生 | docs/13 §1 目标"吊销即失效" | R2 前**不阻断**：已吊销实例仍可当委托人/受托人 | 已修（§4 修复 2、3；`test_revoked_instance_cannot_be_delegation_party`） |

### 1.3 `create_delegation`（[:990-1109](../../agent_explainer/secretariat.py#L990-L1109)）

| # | 核对项 | 规格锚点 | 运行时事实（实测） | 判定 |
| --- | --- | --- | --- | --- |
| D1 | 双方必须是 DID | docs/12:112 | `_require_loose_did`（四段，[:138](../../agent_explainer/secretariat.py#L138)）→ `DELEGATION_INVALID`/400 | 成立（contract `test_delegation_did_must_be_well_formed`） |
| D2 | Agent 委托人必须有出处 | docs/12:112 | `_agent_did_source`（[:1162](../../agent_explainer/secretariat.py#L1162)）：已登记实例 或 某条委托的受托人；否则 400 | 成立（contract `test_delegator_agent_must_have_a_source`） |
| D3 | 父委托存在 + 链连续 | docs/13 §1 #6 | `parent.delegatee == delegator`，否则 `DELEGATION_INVALID`（[:1050](../../agent_explainer/secretariat.py#L1050)） | 成立（`test_delegation_requires_chain_continuity`） |
| D4 | 权限只能收窄 | docs/12:109 | `_check_narrowing`：`action_types`/`tools` 子集 + `max_risk` 不升档 → `SCOPE_WIDENED` | 成立(偏状态码：运行时 **409**，docs/12:109 写 400 = B04) |
| D5 | 深度 ≤ 5 | docs/12:110 | `depth = parent.depth+1 > 5` → `DEPTH_EXCEEDED`；第 6 层实测拒 | 成立(偏状态码：409 vs 400 = B04) |
| D6 | 过期即失效 | docs/12:111 | 叶子过期 → `authorise` 返回 `{allowed:false, reason:DELEGATION_EXPIRED}` | 成立（`test_expired_delegation_denied`） |
| D7 | 过期根不能续期（祖先过期） | docs/12:111 的语义延伸 | R2 前：只查叶子自身 → 过期父委托可继续长出子委托、`authorise` 也只看叶子 | 已修（§4 修复 4；`test_expired_parent_blocks_new_child_and_authorise`） |
| D8 | 同 DID 自委托 | docs/12:112 语义（"人 → 主 Agent → 子 Agent"） | R2 前**无检查**：`a1 → a1` 建出 depth=2 空转层，深度上限变成刷层数工具 | 已修（§4 修复 1；`test_boundary_self_delegation_is_rejected`） |
| D9 | `ttl_seconds` 类型 | docs/12:201（T4 字段） | R2 前 `int("abc")` → `ValueError` 冒到服务端 500；`ttl=0` 被当"没传"→ 默认 30 天（凭空多给一个月） | 已修（§4 修复 5；`test_ttl_seconds_type_is_validated`） |
| D10 | 委托签名可追溯 | docs/13 §1 #6 的精神（责任链每段要有出处） | R2 前：一律用 `user:<delegator>` 的**本地新建钥匙**签 → 已登记 Agent 当委托人时，签名与身份账本公钥对不上（自证） | 已修（§4 修复 6；`test_delegation_signed_with_delegator_own_identity_key`） |
| D11 | 未登记 user DID 可直建根委托 | docs/12:112"委托人还必须是已登记的 DID（人或 Agent）" | 实测：OWNER 从未登记身份仍建出 depth=1 根委托（`delegator_did` 非 `did:tracelens:agent:` 分支不查出处） | **未修 = B08**（理由与方案见 §7） |

### 1.4 `sign_record` 的责任链校验（[:634-722](../../agent_explainer/secretariat.py#L634-L722) + `_validated_chain` [:724-762](../../agent_explainer/secretariat.py#L724-L762)）

| # | 核对项 | 规格锚点 | 运行时事实（实测） | 判定 |
| --- | --- | --- | --- | --- |
| C1 | 链尾 = 实例 DID（不是 `agent_id`） | docs/12:104 / A5 | `agent_did_of(agent_pubkey)`（[:671](../../agent_explainer/secretariat.py#L671)）；不等 → `DELEGATION_INVALID`/400 | 成立（contract `test_delegation_chain_uses_instance_did_not_agent_id`、探针 tail≠实例 400） |
| C2 | 链根 = 账本 `owner_did` | docs/12:106 / A5 | 不等 → 本轮改为 `FIELD_INVALID` + `field=human_owner`（[:751-756](../../agent_explainer/secretariat.py#L751-L756)），400 | 已修（B07；探针 `C 链根≠owner (B07) -> 400 FIELD_INVALID field=human_owner`） |
| C3 | 入参伪造 `human_owner` | docs/12:108 / A5 | 记录自称 ≠ 账本 → 拒（[:663-669](../../agent_explainer/secretariat.py#L663-L669)），`detail={ledger,record}` | 成立（contract `test_forged_owner_or_chain_is_rejected`） |
| C4 | 链长度 2–8 | docs/12:105 | `<2` 与 `>8` 都拒（[:737-744](../../agent_explainer/secretariat.py#L737-L744)） | 成立 |
| C5 | 链内同段 DID 重复 | docs/12:104"中间是逐层委托的受托人" | 本轮新增：重复 → `DELEGATION_INVALID`/400 `detail.duplicated`（[:745-750](../../agent_explainer/secretariat.py#L745-L750)） | 已修（自委托写进记录的入口关掉） |
| C6 | 每段过严格 DID pattern | docs/12:113 | `_require_record_did`（写进已签名记录的 DID 用 schema `$defs/did` 同形） | 成立 |
| C7 | 中间段是否真来自委托账本 | docs/12:104 | **不校验**：中间段只验形状/顺序/根尾，不查它在委托账本里存在 | 残余风险 §6-3 |
| C8 | 已吊销再签 | A3 | `IDENTITY_REVOKED`（运行时 409） | 成立(偏状态码 = B06) |

### 1.5 `authorise` / `verify_record` 的责任链侧（[:1175-1215](../../agent_explainer/secretariat.py#L1175-L1215) / [:780-869](../../agent_explainer/secretariat.py#L780-L869)）

| # | 核对项 | 运行时事实（实测） | 判定 |
| --- | --- | --- | --- |
| A1 | `authorise` 逐层收窄 | 叶子 scope 与父 scope 的收窄在**建委托时**已强制（D4），故叶子 ⊆ 祖先，逐层检查等价 | 成立 |
| A2 | 过期判定覆盖整条链 | 本轮新增祖先遍历，`detail.scope="ancestor"` 指认过期层（[:1193-1200](../../agent_explainer/secretariat.py#L1193-L1200)） | 已修 |
| A3 | `verify_record` 复算链 | 每段 DID pattern + 根=`human_owner` + 尾=`agent_did_of(pubkey)`（[:856-877](../../agent_explainer/secretariat.py#L856-L877)） | 成立 |
| A4 | 委托签名复算 | `authorise` 不复算委托签名（防篡改由 `Ledger.verify()` + §4 修复 6 的签名可绑定身份公钥覆盖） | 残余风险 §6-4 |

---

## 2. 自查边界四项（各一个可复跑断言）

复跑单条：
`python -m unittest tests.test_secretariat.SecretariatR2BoundaryTest.test_boundary_<名称> -q`

| 自查项 | 断言（文件:测试名） | 实测 |
| --- | --- | --- |
| ① 伪造 `human_owner` | [tests/test_secretariat.py](../../tests/test_secretariat.py) `SecretariatR2BoundaryTest.test_boundary_forged_human_owner_is_field_invalid` | `400 SECRETARIAT_FIELD_INVALID field=human_owner detail={ledger,record}` |
| ② 链尾非本实例 DID | 同上 `test_boundary_chain_tail_must_be_this_instance_did` | `400 SECRETARIAT_DELEGATION_INVALID detail={tail,agent_did}`；顺带钉住链根（B07→400 FIELD_INVALID/human_owner）与链内重复段 |
| ③ 同 DID 委托 | 同上 `test_boundary_self_delegation_is_rejected` | `400 SECRETARIAT_DELEGATION_INVALID reason=self_delegation`；根/子委托两路都拒；**被拒不留账本行**（`len(delegations_ledger)==1`） |
| ④ 已吊销再签 | 同上 `test_boundary_sign_after_revoke_is_refused` | `409 SECRETARIAT_IDENTITY_REVOKED`（码成立；400/409 差异 = B06，见 §5） |

---

## 3. 命令与输出摘要

工作目录 `agent-explainer`，垫片：`$env:TMP="$PWD\.tmp"; $env:TEMP=$env:TMP; $env:PYTHONPATH="$PWD\.tmp\pyfix"`（垫片不进交付物）。

| 命令 | 输出（真实） |
| --- | --- |
| `python -m unittest tests.test_secretariat tests.test_secretariat_contract -q` | `Ran 62 tests in 1.242s` / `OK` / `EXIT=0` |
| `python -m unittest tests.test_secretariat -q` | `Ran 49 tests in 0.946s` / `OK` / `EXIT=0`（R2 前 40） |
| `python -m unittest tests.test_secretariat_contract -q` | `Ran 13 tests in 0.315s` / `OK` / `EXIT=0`（R2 前 12） |
| `python -m unittest tests.test_secretariat_api -q` | `Ran 7 tests in 3.785s` / `OK` / `EXIT=0` |
| `python -m unittest tests.test_secretariat_autosign -q` | `Ran 4 tests in 2.241s` / `OK` / `EXIT=0` |
| `python -m unittest discover -s tests -p "test_*.py" -q` | `Ran 929 tests in 187.300s` / `FAILED (failures=1, skipped=3)`，唯一红 = `test_e2e.ArtifactTests.test_signature_status_is_honest`（r0 §实测已登记：产物状态取值口径，与 R2 无关） |
| `python .tmp\r2_probe.py`（复核探针，垫片不进交付物） | 18 条运行时事实，逐字如下 |

探针关键输出（逐字，节选 12/18 条）：

```text
T5 critical same DID (B05) -> http=400 code=SECRETARIAT_FIELD_INVALID field=approvers detail={'required': 2, 'given': 1, 'supplied': 2, 'risk_level': 'critical', 'reason': 'duplicate approver DID'}
T5 critical no approvers -> http=409 code=SECRETARIAT_APPROVAL_REQUIRED field=approvers detail={'required': 2, 'given': 0, 'risk_level': 'critical'}
T5 critical two distinct DID -> OK signed+sealed
A5 forged human_owner -> http=400 code=SECRETARIAT_FIELD_INVALID field=human_owner detail={'ledger': 'did:tracelens:user:y5wt…', 'record': 'did:tracelens:user:vzrg…'}
C 链根≠owner (B07) -> http=400 code=SECRETARIAT_FIELD_INVALID field=human_owner detail={'root': …, 'owner': …}
A5 链尾≠实例 DID -> http=400 code=SECRETARIAT_DELEGATION_INVALID field=delegation_chain detail={'tail': …, 'agent_did': …}
C 链内重复段 -> http=400 code=SECRETARIAT_DELEGATION_INVALID detail={'duplicated': ['did:tracelens:agent:ljcv…']}
A9 越权 scope (B04) -> http=409 code=SECRETARIAT_SCOPE_WIDENED field=scope detail={'widened': {'action_types': ['payment']}}
A9 深度 6 (B04) -> http=409 code=SECRETARIAT_DEPTH_EXCEEDED field=parent_id detail={'depth': 6, 'limit': 5}
R2 同 DID 自委托 -> http=400 code=SECRETARIAT_DELEGATION_INVALID reason=self_delegation
R2 ttl_seconds='abc' / ttl_seconds=0 -> http=400 code=SECRETARIAT_FIELD_INVALID field=ttl_seconds
A9 祖先过期 authorise -> SECRETARIAT_DELEGATION_EXPIRED {'scope': 'ancestor', 'delegation_id': 'dlg_…', 'expires_at': '2026-11-11T10:01:00Z'}
R2 过期父委托建新子委托 -> http=400 code=SECRETARIAT_DELEGATION_INVALID field=parent_id reason=parent_expired
A3 已吊销再签 (B06) -> http=409 code=SECRETARIAT_IDENTITY_REVOKED field=agent_id
R2 委托给已吊销实例 / 已吊销实例派委托 -> http=400 code=SECRETARIAT_DELEGATION_INVALID reason=delegatee_revoked / delegator_revoked
R2 吊销后重登记 -> http=400 code=SECRETARIAT_FIELD_INVALID field=rotate detail={'agent_id': 'revoked-agent', 'revoked_at': '2026-11-11T10:02:00Z'}
```

---

## 4. 修复项（文件 + 行为 + 测试）

全部只在 `agent_explainer/secretariat.py` + `tests/test_secretariat*.py`；**未改** `server.py` 与任何状态码映射表。

| # | 缺陷（严重度） | 文件 / 位置 | 改了什么行为 | 补的测试 |
| --- | --- | --- | --- | --- |
| 1 | **B05**：critical 传同一 DID 两次，去重后不足 → 运行时 `APPROVAL_REQUIRED`/409，Q5 冻结 `FIELD_INVALID`/400（high） | [secretariat.py:1523-1542](../../agent_explainer/secretariat.py#L1523-L1542) `_build_approval` | 拆两支：**给了**审批人但去重后 `<required` → `SECRETARIAT_FIELD_INVALID`（`field=approvers`，`detail.given/supplied/reason=duplicate approver DID`）/400；**根本没给（传空）**且未 `allow_unapproved` → 仍 `SECRETARIAT_APPROVAL_REQUIRED`/409 | 改 [tests/test_secretariat.py](../../tests/test_secretariat.py) `test_critical_risk_requires_two_approvers_and_seal_flag`（补 code/field/400 + 409 那一支）；改 [tests/test_secretariat_contract.py](../../tests/test_secretariat_contract.py) `test_critical_needs_two_distinct_approvers`（409→`FIELD_INVALID`+field+400+`given/supplied`）；新增 `test_missing_approvers_stay_approval_required`（空数组→409） |
| 2 | **自委托**：`delegator == delegatee` 无检查，责任链凭空长一层、深度上限可被自刷（medium-high） | [secretariat.py:1006-1013](../../agent_explainer/secretariat.py#L1006-L1013) | 同 DID → `DELEGATION_INVALID`/400 `reason=self_delegation`；根委托与子委托同一条检查 | `test_boundary_self_delegation_is_rejected`（含"被拒不留账本行"） |
| 3 | **已吊销实例仍可参与委托**：吊销终局只挡签名，不挡派责任（high） | [secretariat.py:1014-1035](../../agent_explainer/secretariat.py#L1014-L1035) + `_agent_did_source` [:1162](../../agent_explainer/secretariat.py#L1162) / `_identity_for_agent_did` [:1147](../../agent_explainer/secretariat.py#L1147) | 委托人/受托人是**已登记且已吊销**的实例 DID → `DELEGATION_INVALID`/400 `reason=delegator_revoked / delegatee_revoked`；吊销状态按**公钥逐代**取账本最后一条（轮换掉的旧代不会被"最新一条"掩盖） | `test_revoked_instance_cannot_be_delegation_party` |
| 4 | **过期授权可续期**：`create_delegation` 不查父委托过期；`authorise` 只查叶子自身（medium-high） | [secretariat.py:1056-1064](../../agent_explainer/secretariat.py#L1056-L1064)、[:1193-1200](../../agent_explainer/secretariat.py#L1193-L1200) | 过期父委托建新子委托 → `DELEGATION_INVALID`/400 `reason=parent_expired`；`authorise` 遍历祖先，任一层过期 → `DELEGATION_EXPIRED` + `detail.scope="ancestor"` | `test_expired_parent_blocks_new_child_and_authorise` |
| 5 | **`ttl_seconds` 坏类型冒 500 / `ttl=0` 变 30 天**（medium） | [secretariat.py:240-262](../../agent_explainer/secretariat.py#L240-L262) `_coerce_ttl` + [:1071-1074](../../agent_explainer/secretariat.py#L1071-L1074) | 只接受整数秒（bool/浮点/字符串/容器都拒）→ `FIELD_INVALID`/400 `field=ttl_seconds`；显式 `0` 不再被当成"没传"；不传仍是默认 30 天 | `test_ttl_seconds_type_is_validated`（`0/-1/"abc"/True/1.5/[]/{}` + 默认到期时间断言） |
| 6 | **委托签名是"替委托人现造钥匙"的自证**：已登记 Agent 当委托人时签名公钥与身份账本对不上（medium，A4/责任链出处） | [secretariat.py:1087-1105](../../agent_explainer/secretariat.py#L1087-L1105) | 委托人是**已登记未吊销实例** → 用该实例身份对应私钥（keyring `agent:<agent_id>`）签，`pubkey` 与身份账本一致，新增 `signer={kind:"agent_identity",agent_id}`；否则本地 DID 钥匙 + `signer.kind="local_key"` 并在 `note` 里如实写明"只证明本地记录，不证明真实意愿" | `test_delegation_signed_with_delegator_own_identity_key`（kind/pubkey 对齐 + 委托签名复算通过 + 事后改 `scope` 复算失败） |
| 7 | **B07**：链根不等于 `owner_did` 时报 `DELEGATION_INVALID`，冻结规格写 `FIELD_INVALID(field=human_owner)`（medium） | [secretariat.py:751-762](../../agent_explainer/secretariat.py#L751-L762) | 链根 → `FIELD_INVALID` + `field=human_owner`（400 不变）；链结构问题（长度/重复段/链尾）保持 `DELEGATION_INVALID`；新增"链内同段 DID 重复"拒绝（[:745-750](../../agent_explainer/secretariat.py#L745-L750)） | contract `test_forged_owner_or_chain_is_rejected`（根→FIELD_INVALID/human_owner、尾→DELEGATION_INVALID、重复段→DELEGATION_INVALID）；`test_boundary_chain_tail_must_be_this_instance_did` |
| 8 | 吊销后重登记把"已吊销"报成"信息不同"（low，口径不实） | [secretariat.py:513-522](../../agent_explainer/secretariat.py#L513-L522) | 如实报"已被吊销，重登记不复活；要新实例请显式 `rotate=True`"，码/状态不变（T2 专属码只有 `FIELD_INVALID`，docs/12:199） | `test_re_register_after_revoke_says_revoked`（含 rotate 是唯一出口、新实例可继续签名并验通过） |

新增测试合计：`tests/test_secretariat.py` +9（新类 `SecretariatR2BoundaryTest`）、`tests/test_secretariat_contract.py` +1；改动既有断言 3 处（B05 两处 + B07 一处）。

---

## 5. 契约偏差（不改运行时，交 lead 收口时改规格）

**单列给 lead 的建议条目（B04 家族，含 B06）**：

1. `SECRETARIAT_SCOPE_WIDENED`、`SECRETARIAT_DEPTH_EXCEEDED` 运行时 **409**
   （[secretariat.py:204-207](../../agent_explainer/secretariat.py#L204-L207) `_status_for`），
   而 [docs/12:109-110](../12-secretariat-delta-spec.md#L109-L110) 与
   [server.py:544-547](../../agent_explainer/server.py#L544-L547) `EXPANSION_ERROR_TABLE` 都写 **400**；
   实现侧测试已把 409 钉成契约（[tests/test_secretariat_api.py:107](../../tests/test_secretariat_api.py#L107)）。
   **建议：规格与对外错误表改成 409（状态冲突语义正确），不改运行时。**
2. 同类外溢还有两处，一起收：`SECRETARIAT_IDENTITY_REVOKED` 运行时 409 vs
   [docs/12:236](../12-secretariat-delta-spec.md#L236)（A3）写 400（B06）；
   `SECRETARIAT_IDENTITY_MISSING` server 表 409 vs 运行时 404（B09）。
3. **B05 已按 Q5 收口**：同 DID 双人现在是 `FIELD_INVALID`/400（§4 修复 1），
   [docs/13:87](../13-r2-r3-production-report.md#L87) 里"400 `SECRETARIAT_APPROVAL_REQUIRED`"这句要改成
   "400 `SECRETARIAT_FIELD_INVALID`（field=approvers）"；"一个审批人都没传"仍是 409 `APPROVAL_REQUIRED`。
   [docs/12:111](../12-secretariat-delta-spec.md#L111) 建议补一句：过期判定覆盖**整条委托链**（祖先过期同样拒）。

---

## 6. 残余风险

1. **未登记 user DID 可直建根委托（B08，docs/12:112）**：根委托的责任链根没有登记出处，
   只能靠 `signer.kind="local_key"` + `note` 如实标注（§4 修复 6）。修它需要先定义"user 登记"语义；
   现网 T4 流程（api 测试 `test_delegation_narrowing`）就是从未登记 OWNER 直接建根委托，
   强制会打断该流程，且 `tests/test_secretariat_api.py` 不在本轮写作用域 → 见 §7。
2. **轮换后旧代实例不受"当前吊销"约束**：吊销写的是当时那一代公钥；旧代公钥在账本里没有
   `revoked_at`，`sign_record` 也只看 Agent 最新身份。轮换 + 吊销的组合要让"整人生效"需要新规格条目。
3. **链中间段不查委托账本**（C7）：`delegation_chain` 的中间 DID 只验形状/根/尾/长度/不重复，
   不证明每一段真有一条委托记录。离线验签只需要这些字段，因此属边界；面板/认定书文案不得暗示
   "链的每一段都已核对委托账本"。
4. **`authorise` 不复算委托签名**：防篡改目前靠 `status()["chains"]["delegations"]` 的 `Ledger.verify()`
   整链复算；§4 修复 6 + 新测试证明"事后改 scope 必然签名复算失败"，但 `authorise` 本身不做逐条验签。
5. **`auto_register=True` 让首条记录自报链根**（`_resolve_identity` [:616-631](../../agent_explainer/secretariat.py#L616-L631)）：
   身份未登记的 Agent 首次签名时，链根取自记录里的 `human_owner`（缺省 `did_from_name("local", kind="user")`）。
   已登记身份不受影响（C3）。R4 入库即签名走的正是这条路，属 R4 集成边界。
6. **诚实边界不变**：本地 keyring（`degraded=["local-tsa","in-process-keys"]`）、审批签只证明"本地记录并签名"、
   审批签与身份绑定不可离线复算（docs/12 §5/§10）。

---

## 7. 本轮未做（越界项，交 lead）

- **B08 强校验"委托人必须是已登记 DID（人或 Agent）"**：需要产品口径先定
  （"user 登记"= 出现在身份账本 `owner_did` 里？= keyring 里有 `user:<did>`？= 新增 T2 用户登记端点？）。
  定完口径的最省事实现：根委托要求 `delegator_did` 出现在身份账本任一记录的 `owner_did` 中，
  否则 `DELEGATION_INVALID`/400；同时把 api 测试的 T4 用例改成"先登记身份再建根委托"。
- **`r3-review.md`**：lead 在 T2 指派里点名要 `docs/audit/r3-review.md`，但 T2 的写作用域只含
  `docs/audit/r2-review.md`（任务卡 + 团队任务 t2）。B04/B06 收口条目已在 §5 单列，R3 侧（B05 修复、
  11 域篡改、schema、认定书离线复算）在 t3 复核时成文，路径随 t3 报告发出。
