# v3 C-01～C-04 最终文档整改记录

- 日期：2026-09-24
- 输入复审：`docs/hydraulic-twin/reviews/v3-schema-semantics-review-round-3.md`
- 状态：**v3 候选 / `R0_ROUND_3_FAILED` / `NOT_APPROVED` / `ALL_IMPLEMENTATION_WORK_BLOCKED`**
- 边界：本文件是整改记录，不是独立 reviewer artifact，也不是 R0 PASS/批准 artifact；不得据此标记 Frozen。

## 1. C-01：24h generation 与 resource 幂等引用

采用已冻结的 generation 方案：

- `hydraulic.simulations` 与 `hydraulic.calibration_runs` 各自使用单列 `idempotency_record_id UUID NOT NULL FK -> idempotency_records.id`；
- 两表各自使用 `UNIQUE(idempotency_record_id)`，删除 `submit_idempotency_record_id`、`cancel_idempotency_record_id`、resource `idempotency_key` 列以及永久 `UNIQUE(requested_by,idempotency_key)`；
- `PRD_SUPPLEMENT.md` D-12、simulation/calibration DDL、`idempotency_records` 说明、§17 handoff predicate 与 §18 blocker 同步为同一语义；
- simulation cancel 使用 `scope=simulation.cancel` 的 idempotency record 保存 `resource_id/response`，在 cancel transaction 内验证，不给 simulations 增加第二个 resource FK；
- 24h 后相同 key 通过新 generation record 和新的 resource `idempotency_record_id` 创建新 simulation/calibration resource；旧 retired generation 保留审计。

## 2. C-02：四张 durable job 表的 retry CHECK

`network_jobs`、`simulations`、`calibration_runs`、`nowcast_runs` 的冻结 DDL 现在分别显式包含：

```text
CHECK ((status='retry_wait') = (next_attempt_at IS NOT NULL))
CHECK (
  (status='retry_wait' AND retry_backoff_seconds IS NOT NULL AND retry_backoff_seconds>0
   AND last_retry_error_code IS NOT NULL AND btrim(last_retry_error_code)<>'')
  OR
  (status<>'retry_wait' AND retry_backoff_seconds IS NULL AND last_retry_error_code IS NULL)
)
```

§6.1、G1 schema manifest 谓词与 §18 blocker 同步要求两类 CHECK；retry_wait 时 backoff/error 必须完整，非 retry_wait 时必须清空。

## 3. C-03：54 个授权 Settings 与 47 个 preserve-only 字段

采用授权子集方案：

- handoff fixture 的 `settings.fields[]` **只**含 §12 的 54 个 `authorized=true` 字段，并逐项记录 canonical type/default/constraints/validators/env_example/invalid_cases；
- `settings.unauthorized_fields[]` 单独记录其余 47 个字段，每项只允许 `name/authorized=false/preserve_only=true`；
- §12 冻结完整 47 项 preserve-only 名称清单；两组互斥、并集必须等于 101 项 `Settings.model_fields`，并与 `.env.example` 101 个 key 完全相等；
- checker 只对 54 个授权字段断言完整 Settings 相等；47 个非授权字段必须存在，但不得被 handoff 声称为本轮授权；
- §17 predicate 与 §18 blocker 同步使用两个数组，不再把 101 项完整 inventory 塞入 `settings.fields[]`。

## 4. C-04：删除不可回溯的候选文档 hash 自述

- PRD_SUPPLEMENT 与 IMPLEMENTATION_CONTRACT 已删除历史 review 文件 hash、历史三文档输入 hash 和历史 document bundle 值；
- 两份候选文档只保留 reviewer/verdict 与 review 路径，不内嵌旧 hash；
- 下一轮 reviewer 必须从 live files 重算；只有最终批准 artifact 可以记录该当轮可重算 bundle；
- 本整改记录同样不记录整改后 bundle 值，避免再次形成自引用。

## 5. 未回退的前轮通过项

本轮保留并复核：

- 单一 durable-job transition table；
- `worker_slots` 空闲/busy CHECK、seed 与 cancelling/terminal 释放；
- frame registration/hypertable 唯一性与 target-manifest 唯一约束；
- 三类独立 hash + target-manifest hash；
- forecast transition、observation trust、工件 fail-closed、reserved-slot 禁借；
- `DEP-AUTH-01..13`、owner 矩阵、`current|post-handoff` phase 词汇、`DOC-OWNERSHIP` 空集；
- 26-node blockedBy 图、§4 PASS 项与 R0 PASS 前 migration 零变更。

## 6. 本轮实际静态验证

执行的只读语义断言输出：

```text
final_semantic_check=PASS
C01_resource_fk=3/3 unique=3/3 legacy_key_columns=0
C02_next=4/4 metadata=4/4
C03_fields=101 authorized=54 unauthorized=47 env=101
C04_stale_bundle_values=0
previous_anchors=PASS transition_tables=prd:0,ic:1
owner_rows=119 candidate_paths=48 unmatched=0 ambiguous=0
blockedBy_nodes=26 unknown=0 cycles=0 DOC-OWNERSHIP=empty-set
candidate_status=NOT_APPROVED migrations=empty
source_prd_sha256=11e56e68a68017b84b5ae577dede8a74f6217d4a7877f5e358a18f14e66807ee
```

7 个 gate/checker/harness 入口仍为 MISSING，`test:perf:twin=None`。该检查读取三份主文档、整改记录、`backend/app/core/config.py`、`.env.example` 与 `git status`，验证 C-01～C-04、Settings 54/47 分区和前轮关键锚点；没有写生产代码、依赖实现或 migration。

## 7. 未运行

新的独立复审、官方 owner/handoff/migration checker、G0～G6、AC-1～AC-8、pytest、Vitest、npm ci、Alembic live、Docker、solver/worker/calibration、性能和 backup/restore 均未在本整改轮运行，保持 NOT RUN。静态文档断言不能替代独立 R0 verdict 或实现门禁。
