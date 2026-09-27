# 结果存储与参数率定实施摘要

## 范围与契约依据

本实现对应任务“结果存储与参数率定”，交付 attempt-scoped 结果 repository、受控产物索引、Timescale 帧保留边界、基于 Influx `sensor_readings.water_level_mm` 的 RMSE/NSE、网格/随机搜索、前后改善报告和最优参数。源 PRD 要求节点/管段/积水按不超过 5 分钟帧序列化、TimescaleDB 90 天保留、RMSE/NSE 率定与 AC-5 量化改善（`docs/hydraulic-twin/SOURCE_PRD.md:20,32`）。冻结补充契约要求流式写 Timescale、禁止先在内存积累全部帧（`docs/hydraulic-twin/PRD_SUPPLEMENT.md:157-164`），并规定 AC-5 的 `NSE delta >= 0.05`、`RMSE <= 0.95 * baseline`、峰值误差不恶化超过 10%、无质量失败（`docs/hydraulic-twin/PRD_SUPPLEMENT.md:453-480`）。实施契约固定 registration + hypertable frame 同事务、发布指针、90 天仅清理 frame 而保留 summary/audit（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:770-816`），以及 calibration run/report 的数据集、指标和原子 finalize 语义（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:898-951`）。本任务文件所有权由 `IMPLEMENTATION_CONTRACT.md:2284-2301` 指定给 `result-calibration`。

## 真实改动

### `backend/app/services/hydraulic/results.py`

- 新增冻结的 300 秒最大帧间隔和 90 天默认保留常量；`FrameWriteContext` 固定 simulation/attempt/revision/target manifest/solver/fencing identity，只有 `1..300` 秒 reporting interval 可进入写入路径（`results.py:38-40,92-136`）。
- 新增 canonical UTF-8 JSON 帧序列化、payload byte count 和 SHA-256；拒绝 NaN/Inf、超过单帧 64 MiB 上限、错时间网格和超出冻结 frame count 的输入（`results.py:232-292`）。
- 新增 `ResultRepository.write_frame`：锁定 attempt，验证 fencing/lineage，先查 exact replay，再写普通 PostgreSQL `SimulationFrameRegistration` 与 Timescale ORM `SimulationFrame`，一次 `flush` 后交由调用方事务提交；同 index/time/hash 幂等，不同 hash 返回 `FRAME_ATTEMPT_CONFLICT`（`results.py:390-542`）。
- 新增 `ResultRepository.finalize`：要求冻结 `expected_frame_count`，逐项核对 registration/Timescale row、dense index、5 分钟网格、payload hash/bytes 和 frame quality；锁定 simulation 后写不可变 JSON artifact index、`SimulationResult` summary/quality、attempt manifest/hash 和 published simulation pointer，并清除 simulation lease（`results.py:544-707`）。
- 产物写入仅允许 `simulation-results` / `calibration-reports` 两个 category 和 UUID 文件名；先写/fsync 临时文件，再用 hard link 原子发布，内容冲突 fail closed（`results.py:321-387`）。
- 新增 90 天 retention 分类和 sweeper：只删除 `frame_time < cutoff` 的 `simulation_frames`，不删除 `simulation_results`、attempt registration/manifest 或 artifact（`results.py:295-318,708-721`）。

### `backend/app/services/hydraulic/calibration.py`

- 新增强类型观测/模拟/候选/指标/参数/试验/报告模型；观测 measurement/field 固定为 `sensor_readings.water_level_mm`，默认 12 个 paired observations、3 个独立事件（`calibration.py:31-56,88-438`）。
- 新增同 target、默认 300 秒容差的一对一时间配对，计算 RMSE、NSE、Bias、peak absolute error、missing rate；NSE 分母为零时返回 `nse=None` 和 `NSE_UNDEFINED`，不伪造 0/1（`calibration.py:444-510,858-911`）。
- 新增最小化目标 `weighted-water-level-error-v1 = rmse_weight*RMSE + nse_weight*(1-NSE) + peak_error_weight*peak_abs_error`；NSE 未定义使用固定 penalty 1.0（`calibration.py:235-276,513-526`）。
- 新增确定性网格笛卡尔积和 seeded 随机搜索；参数名/边界/step 固定，grid 候选数超出预算时拒绝截断，相同 seed 生成相同候选和报告（`calibration.py:279-337,936-967`）。
- 新增 baseline/candidate 前后报告、最优参数及其 hash、dataset hash、provenance、objective 和 AC-5 四项阈值判定；`synthetic/mock/external_unverified` 可量化诊断改善但 `ac5_eligible=false`、`improved=false`（`calibration.py:529-690,970-1014`）。
- 新增 `InfluxWaterLevelObservationSource`，使用有界 Flux 查询读取 `sensor_readings.water_level_mm`；旧 Influx 点没有稳定 source event identity，因此固定为 `external_unverified`，且不会按时间戳伪造独立降雨事件（`calibration.py:714-819`）。
- 新增 canonical calibration report 产物写入，报告 bytes 的 SHA-256 等于 `CalibrationReport.content_sha256`（`calibration.py:383-438,693-705`）。

### `backend/tests/hydraulic/test_results_calibration.py`

新增 13 个聚焦测试，覆盖：

- canonical 帧序列化、301 秒拒绝、24h/5min 恰好 index `0..288` 共 289 帧（`test_results_calibration.py:162-198`）；
- registration + Timescale frame 单 flush、相同 hash 幂等、异 hash 冲突、frame gap/错 grid 拒绝（`test_results_calibration.py:200-265`）；
- finalize 发布 summary/quality/artifact index、attempt 指针、simulation completed/100 和 lease 清理（`test_results_calibration.py:268-343`）；
- 90 天精确边界、只删除 frame、不删除 result/attempt，以及受控产物不可变（`test_results_calibration.py:346-388`）；
- 合成 3 事件/36 点数据量化 RMSE `10.0 -> 0.0 mm`、NSE `-> 1.0`、RMSE 降低 100%，同时禁止 synthetic 冒充 AC-5（`test_results_calibration.py:390-461`）；
- 完整 provenance 的 verified fixture 同时满足 NSE/RMSE/peak/quality 四项门槛（`test_results_calibration.py:464-504`）；
- seeded 随机搜索可复现、NSE 零分母、Influx 字段查询与指标联通、报告产物 hash（`test_results_calibration.py:507-632`）。

## 实际执行的验证命令与结果

所有命令均在 `smart-city-drainage/backend` 执行；测试使用 Python 3.11 和 `requirements-cpu.txt`。

1. 任务要求的聚焦测试：

```text
$ uvx --python 3.11 --from pytest==8.0.1 --with pytest-asyncio==0.23.6 --with-requirements requirements-cpu.txt pytest tests/hydraulic/test_results_calibration.py -q
13 passed, 1 warning in 0.14s
exit 0
```

2. 全部 hydraulic 服务测试（回归检查，不替代聚焦测试）：

```text
$ uvx --python 3.11 --from pytest==8.0.1 --with pytest-asyncio==0.23.6 --with-requirements requirements-cpu.txt pytest tests/hydraulic -q
160 passed, 1 warning in 1.47s
exit 0
```

3. owned 文件质量检查：

```text
$ uvx --python 3.11 --from black==24.1.0 black --check app/services/hydraulic/results.py app/services/hydraulic/calibration.py tests/hydraulic/test_results_calibration.py
All done! ✨ 🍰 ✨
3 files would be left unchanged.
exit 0

$ uvx --python 3.11 --from isort==5.13.0 isort --check-only app/services/hydraulic/results.py app/services/hydraulic/calibration.py tests/hydraulic/test_results_calibration.py
exit 0

$ uvx --python 3.11 --from flake8==7.0.0 flake8 app/services/hydraulic/results.py app/services/hydraulic/calibration.py tests/hydraulic/test_results_calibration.py
exit 0

$ uvx --python 3.11 --from mypy==1.8.0 --with sqlalchemy==2.0.27 --with pydantic==2.6.1 --with pydantic-settings==2.1.0 --with influxdb-client==1.41.0 mypy --follow-imports=skip app/services/hydraulic/results.py app/services/hydraulic/calibration.py
Success: no issues found in 2 source files
exit 0
```

4. 全后端 pytest 也实际运行，但不是本模块通过声明：

```text
$ uvx --python 3.11 --from pytest==8.0.1 --with pytest-asyncio==0.23.6 --with-requirements requirements-cpu.txt pytest -q
6 failed, 309 passed, 4 skipped, 1 warning in 21.08s
exit 1
```

6 个失败均在 ownedPaths 外：

- `test_live_migration_gate_passes_without_running_alembic` 与 `test_migration_gate_cli_accepts_workspace_prefixed_paths`：现有 `backend/alembic/versions/0002_hydraulic_schema.py` 相对 base 路径集变化；
- `test_live_dependency_handoff_matches_fixture_and_settings` 与 `test_dependency_handoff_cli_accepts_workspace_prefixed_paths`：现有 `.env.example` 与 handoff SHA-256 不一致；
- `test_frontend_performance_entrypoint_remains_deferred`：并行工作区已存在 `frontend/performance/run-twin-map.mjs`；
- `test_sqlite_cascade_path_preserves_legacy_tables`：并行 schema 的 `result_retention_policies.id DEFAULT uuid_generate_v4()` 不能在 SQLite 执行。

本任务未修改上述文件，也未把全后端结果写成 PASS。

## 风险与未运行项

1. **未运行 live PostgreSQL/TimescaleDB**：repository 测试使用最小 async-session fake，验证 ORM row、SQL 作用域和一次 flush，但没有证明真实 Timescale hypertable、FK/check、同事务提交和 90 天 policy 在 live DB 上通过。
2. **未运行 live InfluxDB**：`InfluxWaterLevelObservationSource` 的 Flux 文本和解析由 fake query API 验证；没有真实 bucket/token/field 数据。旧 Influx 点按冻结契约保持 `external_unverified`，不能使 AC-5 PASS。
3. **没有真实 external_verified 率定数据**：合成观测只证明目标函数、搜索和报告数学上可量化改善；标为 `external_verified` 的测试 fixture 也只是算法门禁，不是产品 AC-5 证据。
4. **未接入 durable calibration worker/API**：本任务实现纯率定服务和 report artifact；`CalibrationRun` claim/retry/timeout/finalize、API submit/idempotency 和 candidate simulation orchestration 属于后续 owner。
5. **文件工件与数据库不能形成单一 ACID 事务**：artifact 在 DB commit 前原子发布；若随后 DB transaction 失败，可能留下不可变 orphan artifact，需要 artifact sweeper 按 DB 引用清理。并发 finalizer 已通过 simulation row lock 串行化。
6. **随机搜索是有限预算启发式**：固定 seed 可复现，但不承诺全局最优；网格搜索必须完整枚举，候选数大于 `max_evaluations` 时 fail closed。
7. 本任务未提交、未推送，也未修改 ownedPaths 外的生产、schema、API 或依赖文件。
