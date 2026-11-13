# TraceLens 5.0 · 错误码 → 文案键映射（UX Copy Map）

> 生成物：由 `var/gen_copy_map.py` 从 `tools/i18n_lint.py --json` 的报告生成，**不要手改**。
> 重跑：`python tools/i18n_lint.py --json var/i18n-report.json --quiet && python var/gen_copy_map.py`

## 1. 规则（三条）

| # | 规则 | 依据 |
| --- | --- | --- |
| 1 | 机器码 → 键名固定转换：`<域>.error.<code 小写>`（例 `ROLLBACK_NOT_REVERSIBLE` → `rollback.error.rollback_not_reversible`） | docs/07 §6.4 |
| 2 | 同一 code 的 zh/en 两键必须同时存在，英文值不得含中文 | i18n lint「字典一致性」 |
| 3 | 键存在时**优先用键**；键缺失才回退服务端 `message`（英文底稿，不是产品文案） | `i18n_lint.collect_error_codes()` |

## 2. 现状（实测，非估计）

- 错误码抽取总数：**164**
- 已有专用文案键：**75**
- 回退到 `message`：**89**
- **5.0 新域缺口：0 个**（secretariat / rollback / evidence / confidence / groups / sharing / analytics / suggestions / compliance / trust 全部有键）
- 抽取器误报：**12** 个（非 UPPER_SNAKE 形状，见 §4）
- 发布闸门：`python tools/i18n_lint.py` 当前 **PASS 5/5**；`--strict-codes` 仍会 FAIL，原因全部是 4.x 遗留 + §4 的误报。

> 结论：**5.0 的文案键不是风险项**；风险在 4.x 遗留与抽取器口径。B1 起只需保证「新抛出的码同批带键」。

## 3. 4.x 遗留待补键（按前缀，**不阻塞 5.0**）

| 前缀 | 含义 | 数量 | 代表性错误码 |
| --- | --- | --- | --- |
| `TRACE_` | 写入/适配器 | 18 | `TRACE_DUPLICATE_ID`, `TRACE_ENUM_INVALID`, `TRACE_FIELD_INVALID` |
| `INGEST_` | 实时接入 | 16 | `INGEST_ADAPTER_REJECTED`, `INGEST_DISABLED`, `INGEST_EMPTY` |
| `AUDIT_` | 审计链 | 16 | `AUDIT_CHAIN_BROKEN`, `AUDIT_CHAIN_FIELD_LOCKED`, `AUDIT_EVENT_TYPE` |
| `IMPORT_` | 导入 | 6 | `IMPORT_FORMAT_UNSUPPORTED`, `IMPORT_INPUT_EMPTY`, `IMPORT_INPUT_NOT_JSON` |
| `EXPLAIN_` | 解释生成 | 5 | `EXPLAIN_GENERATED_BY_INVALID`, `EXPLAIN_LANG_UNSUPPORTED`, `EXPLAIN_LLM_CLIENT_INVALID` |
| `STREAM_` | SSE 流 | 3 | `STREAM_ACCEPT_REQUIRED`, `STREAM_LIMIT_REACHED`, `STREAM_QUERY_INVALID` |
| `AUTH_` | 认证 | 3 | `AUTH_MODE_INVALID`, `AUTH_SECRET_TOO_SHORT`, `AUTH_STATE_CORRUPT` |
| `USER_` | 用户 | 1 | `USER_NOT_FOUND` |
| `PERMISSION_` | 权限 | 1 | `PERMISSION_UNKNOWN` |
| `RECORD_` | 记录 | 1 | `RECORD_STORE_WRITE_FAILED` |
| `INTERVENE_` | 干预 | 1 | `INTERVENE_RECORD_INVALID` |
| `SOURCE_` | 来源归因 | 1 | `SOURCE_REPORTED_FAILURE` |
| `EXPORT_` | 导出 | 1 | `EXPORT_FORMAT_INVALID` |
| `SCHEMA_` | 数据契约 | 1 | `SCHEMA_VERSION_UNSUPPORTED` |
| `OTEL_` | OTEL 适配 | 1 | `OTEL_STATUS_CODE_ERROR` |
| `ADAPTER_` | 适配器 | 1 | `ADAPTER_OUTPUT_INVALID` |
| `STATUS_` | 状态 | 1 | `STATUS_CODE_ERROR` |

合计 **77** 个 4.x 遗留码待补键；**5.0 域缺口 0 个**。

## 4. 抽取器误报（B0 工具修复项）

`tools/i18n_lint.py::collect_error_codes()` 会把字典里的普通单词当错误码收集。实测误报：

`alias_mismatch`、`blank_line`、`chain_break`、`changed_fields`、`count_mismatch`、`entry_not_object`、`hash_mismatch`、`head_mismatch`、`seal_count_mismatch`、`seal_mismatch`、`seq_gap`、`unparsable_line`

**修法（B0，0.25 人天）**：抽取时只接受 `^[A-Z][A-Z0-9_]{3,}$` 形状；保留 `--strict-codes` 语义不变。
修完后 `codes_total` 会从 164 降到约 152，`--strict-codes` 才有意义的基线。

## 5. 5.0 新码准入（B1 起生效）

1. 谁抛新码，谁在**同一个改动**里加 `web/i18n/parts/<域>.zh.json` + `.en.json` 两键；
2. 跑 `python tools/i18n_merge.py` 合并，再 `node agent_explainer/web/selfcheck.mjs --sync-keys --sync-embed`；
3. 批次收尾：`python tools/i18n_lint.py --json var/i18n-report.json --quiet`，检查报告里
   `codes_missing` 中 **5.0 前缀零增长**（脚本：`python var/gen_copy_map.py` 会打印 5.0 缺口数）；
4. 文案口径遵守 `docs/10-honest-downgrade.md` 的禁用短语表。
