# 结果存储与参数率定独立审查

- 审查日期：2026-09-25
- 结论：**不批准（4 high，1 low）**
- 审查方式：只读核对冻结契约、真实实现、任务给出的聚焦测试和实现摘要；未修改实现或测试。
- 决策：`approved=false`。存在会发布不可比较 AC-5 报告、发布非法水力状态、放过超限 Courant 以及使随机率定无限循环的高风险问题。

## 范围与契约依据

任务给出的冻结规格要求：序列化不超过 5 分钟帧，写 Timescale 结果与产物索引；用 `sensor_readings.water_level_mm` 计算 RMSE/NSE；支持网格/随机搜索；输出前后改善报告和最优参数。实际读取并核对了：

- `docs/hydraulic-twin/SOURCE_PRD.md:20,32`：5 分钟帧、TimescaleDB 90 天、RMSE/NSE 率定和 AC-5 量化改善。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:158-164`：单帧 64 MiB、帧必须流式写 Timescale，禁止先积累全部帧。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:254-258`：90 天只删除 frame，summary/attempt/audit 保留。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:343-362`：所有 frame 状态有限且非负，Python solver Courant 硬边界为 `0.5`。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:454-480`：至少 3 个独立事件、预指定 validation/test、baseline/candidate 同条件、报告保存事件切分与完整 provenance。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:770-816`：registration 与 hypertable frame 同事务、完整时间网格、fencing、原子 finalize 和 retention 语义。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:818-896`：观测 trust/auth/binding 与 AC-5 准入；当前 Influx 无稳定 source identity 时必须 fail closed。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:898-951`：calibration run/report 的 dataset、split、metrics 与原子 finalize 契约。
- `backend/app/services/hydraulic/results.py`、`backend/app/services/hydraulic/calibration.py`、`backend/tests/hydraulic/test_results_calibration.py`、`docs/hydraulic-twin/implementation/result-calibration.md` 的真实内容。

补充结构核对：`rg -n "ResultRepository|prepare_frame\\(|run_calibration\\(" backend/app` 只找到服务自身定义，没有生产 worker/API 调用点。因此本报告审查的是公开服务契约本身；它不能证明 live Timescale、live Influx 或 durable calibration 生命周期已经接通。

## 实际执行的聚焦测试

执行任务给出的测试文件，命令与实际输出如下：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && uvx --python 3.11 --from pytest==8.0.1 --with pytest-asyncio==0.23.6 --with-requirements requirements-cpu.txt pytest tests/hydraulic/test_results_calibration.py -q
```

```text
.............                                                            [100%]
============================== warnings summary ===============================
..\..\..\..\..\AppData\Local\uv\cache\archive-v0\41x0ZNIPAy35J9c5\Lib\site-packages\pydantic\_internal\_fields.py:151
  C:\Users\zyq\AppData\Local\uv\cache\archive-v0\41x0ZNIPAy35J9c5\Lib\site-packages\pydantic\_internal\_fields.py:151: UserWarning: Field "model_version" has conflict with protected namespace "model_".
  
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
13 passed, 1 warning in 0.12s
```

聚焦测试通过，但未覆盖下列失败边界。按任务要求未重复最终全量门禁；实现摘要中的历史全量结果不作为本次通过证据。本次也未运行 live PostgreSQL/TimescaleDB 或 live InfluxDB。

## 定向诊断命令与实际输出

以下命令均在 `smart-city-drainage/backend` 执行。

### D1 — AC-5 使用互不重叠的 baseline/candidate 事件仍 PASS

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && uvx --python 3.11 --with-requirements requirements-cpu.txt python - <<'PY'
from datetime import datetime, timedelta, timezone
from app.services.hydraulic.calibration import CandidateEvaluation, ObservationPoint, ParameterSpec, SimulatedWaterLevel, run_calibration
start = datetime(2026,1,1,tzinfo=timezone.utc)
obs=[]
for event in range(3):
    for i in range(12):
        obs.append(ObservationPoint(start+timedelta(days=event, minutes=5*i), 'N-1', 50+10*(i%6), f'event-{event}', trust_level='external_verified'))
event0=obs[:12]
event2=obs[24:]
baseline=[SimulatedWaterLevel(x.observed_at, x.target_id, x.value_mm+10) for x in event0]
report=run_calibration(
    observations=obs, baseline_predictions=baseline,
    parameter_space={'offset_mm': ParameterSpec('offset_mm', 10, 10, 1)},
    evaluator=lambda p: CandidateEvaluation([SimulatedWaterLevel(x.observed_at, x.target_id, x.value_mm) for x in event2]),
    method='grid', max_evaluations=1, require_verified=True,
    provenance={
      'network_revision_id':'rev', 'rainfall_scenario_id':'rain', 'solver_id':'py_mw',
      'solver_version':'1', 'baseline_parameter_sha256':'b'*64, 'evaluation_code_sha256':'c'*64,
    },
)
print(f'report_code={report.report_code} ac5_eligible={report.ac5_eligible} improved={report.improved}')
print(f'baseline_pairs={report.baseline_metrics.sample_count} candidate_pairs={report.calibrated_metrics.sample_count} paired_events=1+1 (disjoint)')
print(f'split_manifest_present={"split_manifest" in report.to_dict()} baseline_rmse={report.baseline_metrics.rmse_mm} candidate_rmse={report.calibrated_metrics.rmse_mm}')
PY
```

```text
report_code=PASS ac5_eligible=True improved=True
baseline_pairs=12 candidate_pairs=12 paired_events=1+1 (disjoint)
split_manifest_present=False baseline_rmse=10.0 candidate_rmse=0.0
```

### D2 — Courant=0.75 被发布质量门禁接受

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && uvx --python 3.11 --with-requirements requirements-cpu.txt python - <<'PY'
import uuid
from datetime import datetime, timezone
from app.services.hydraulic.results import FrameWriteContext, ResultRepository
ctx = FrameWriteContext(
    simulation_id=uuid.UUID(int=1), attempt_id=uuid.UUID(int=2), attempt_no=1,
    network_id=uuid.UUID(int=3), network_revision_id=uuid.UUID(int=4),
    target_manifest_id=uuid.UUID(int=5), target_manifest_sha256='a'*64,
    solver_id='py_mw', solver_version='1', fencing_token=1,
    first_frame_at=datetime(2026,1,1,tzinfo=timezone.utc),
)
ResultRepository._validate_quality_report(ctx, {
    'status': 'verified', 'max_mass_residual_relative': 1e-5, 'max_courant': 0.75,
})
print('accepted_status=verified max_courant=0.75')
PY
```

```text
accepted_status=verified max_courant=0.75
```

### D3 — finalize 发布负水深状态

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && uvx --python 3.11 --from pytest==8.0.1 --with pytest-asyncio==0.23.6 --with-requirements requirements-cpu.txt python - <<'PY'
import asyncio, importlib.util, tempfile
from dataclasses import replace
from pathlib import Path
from app.models.hydraulic_models import SimulationFrame, SimulationFrameRegistration
from app.services.hydraulic.results import FrameInput, ResultRepository, prepare_frame
spec=importlib.util.spec_from_file_location('review_test_support', 'tests/hydraulic/test_results_calibration.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
async def main():
    context=replace(mod._frame_context(), expected_frame_count=1)
    attempt=mod._attempt(); simulation=mod._simulation(); session=mod._RecordingSession(attempt, simulation)
    prepared=prepare_frame(context, FrameInput(frame_index=0, frame_time=context.first_frame_at, payload={'nodes': {'depth_m': [-1.0]}}))
    session.registrations.append(SimulationFrameRegistration(attempt_id=context.attempt_id, frame_index=0, frame_time=prepared.frame_time, payload_bytes=prepared.payload_bytes, payload_sha256=prepared.payload_sha256, quality_status='verified'))
    session.frames.append(SimulationFrame(frame_time=prepared.frame_time, attempt_id=context.attempt_id, simulation_id=context.simulation_id, attempt_no=context.attempt_no, frame_index=0, network_id=context.network_id, network_revision_id=context.network_revision_id, target_manifest_id=context.target_manifest_id, target_manifest_sha256=context.target_manifest_sha256, solver_id=context.solver_id, solver_version=context.solver_version, schema_version=1, payload=prepared.payload, payload_bytes=prepared.payload_bytes, payload_sha256=prepared.payload_sha256, quality_status='verified'))
    with tempfile.TemporaryDirectory() as root:
        await ResultRepository(session, artifact_root=Path(root)).finalize(context, summary={'frame_count':1}, quality_report={'status':'verified','max_mass_residual_relative':1e-5,'max_courant':0.4})
    result=next(x for x in session.added if x.__class__.__name__=='SimulationResult')
    print(f"published_negative_depth={result.summary['frame_count']==1 and prepared.payload['nodes']['depth_m'][0]==-1.0} attempt_status={attempt.status} simulation_status={simulation.status}")
asyncio.run(main())
PY
```

```text
published_negative_depth=True attempt_status=published simulation_status=completed
```

### D4 — 固定参数区间使随机搜索无限循环

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && set +e; timeout 8s uvx --python 3.11 --with-requirements requirements-cpu.txt python - <<'PY'
from app.services.hydraulic.calibration import ParameterSpec, _candidate_parameters
gen=_candidate_parameters((ParameterSpec('x', 1.0, 1.0),), method='random', max_evaluations=2, seed=0)
print(f'first={next(gen)}', flush=True)
print(f'second={next(gen)}', flush=True)
PY
code=$?; printf 'diagnostic_exit=%s (124 means timeout)\n' "$code"; exit 0
```

```text
first={'x': 1.0}
diagnostic_exit=124 (124 means timeout)
```

### D5 — `prepare_frame` 接受非整数 frame index

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && uvx --python 3.11 --with-requirements requirements-cpu.txt python - <<'PY'
import uuid
from datetime import datetime, timedelta, timezone
from app.services.hydraulic.results import FrameInput, FrameWriteContext, prepare_frame
ctx=FrameWriteContext(
 simulation_id=uuid.UUID(int=1), attempt_id=uuid.UUID(int=2), attempt_no=1,
 network_id=uuid.UUID(int=3), network_revision_id=uuid.UUID(int=4),
 target_manifest_id=uuid.UUID(int=5), target_manifest_sha256='a'*64,
 solver_id='py_mw', solver_version='1', fencing_token=1,
 first_frame_at=datetime(2026,1,1,tzinfo=timezone.utc),
)
prepared=prepare_frame(ctx, FrameInput(frame_index=0.5, frame_time=ctx.first_frame_at+timedelta(seconds=150), payload={}))
print(f'accepted_frame_index={prepared.frame_index!r} type={type(prepared.frame_index).__name__}')
PY
```

```text
accepted_frame_index=0.5 type=float
```

## Findings

### H1 — AC-5 可在 baseline/candidate 使用不同事件时错误 PASS

- **严重度：high**
- **位置：** `backend/app/services/hydraulic/calibration.py:570-599,656-688,821-854`；`backend/app/services/hydraulic/calibration.py:408-430`
- **问题：** `run_calibration` 对 baseline 和每个 candidate 分别调用 `compute_metrics`，配对结果没有冻结，更没有验证二者使用同一批 observation/target/timestamp。3-event 门禁只统计全部 eligible observations 中的 event ID，不统计实际 paired events。`ac5_eligible` 只检查 `require_verified`、观测 trust 字符串和几个非空 provenance 字段；没有 split manifest、baseline/candidate simulation lineage、boundary/initial-state/grid 一致性，也不验证 auth/binding 证据。因此可以拿事件 A 的 baseline 与完全不相交的事件 C 的 candidate 做“前后改善”，仍返回 `PASS`。
- **冻结契约：** 至少 3 个独立事件、预指定 validation/test、baseline/candidate 同 network/rainfall/boundary/initial state/solver/evaluation code，报告必须保存事件切分：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:458-479`。只有 external_verified 且 auth/binding 验证的数据可进入 AC-5：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:867-873`。持久化 schema 明确要求 `split_manifest` 和 hash：`backend/app/models/hydraulic_models.py:2021-2067`、`backend/app/schemas/hydraulic_schemas.py:964-973`。
- **测试缺口：** `backend/tests/hydraulic/test_results_calibration.py:463-503` 的“verified”fixture 只改 trust 字符串和 provenance，就能得到 `ac5_eligible=True`，没有 split、simulation IDs 或 paired-event 断言。
- **诊断 D1：** 36 个观测分属 3 个事件；baseline 只预测 event-0，candidate 只预测 event-2。实际输出为 `report_code=PASS ac5_eligible=True improved=True`、两侧各 12 对但事件完全不相交，且 `split_manifest_present=False`；provenance 中普通字符串 `rev`/`rain` 也被接受。
- **建议修复：** 在搜索前根据冻结的 validation/test split 建立一次不可变 evaluation cohort，以 observation identity + target + timestamp 固定配对；要求 baseline 和所有 candidate 使用完全相同的 paired cohort，并按 paired event ID 再验证至少 3 个独立事件。报告/API 必须携带并 hash `split_manifest`、baseline/candidate simulation IDs 及同 network revision、target manifest、rainfall、boundary、initial state、time grid、solver/version、evaluation code 的等价证明；AC-5 只能由可信 ingest/repository 产出的 auth+binding verified observation 打开。

### H2 — ResultRepository 能发布负 depth/volume 的水力状态

- **严重度：high**
- **位置：** `backend/app/services/hydraulic/results.py:231-289,786-878`
- **问题：** `serialize_frame_payload` 只验证可 canonical JSON 化并拒绝 NaN/Inf；`prepare_frame` 不按 `schema_version` 验证任何水力状态。finalize 的 frame-grid 校验只核对数量、index、time、hash/bytes 和 quality enum；质量门禁只检查调用者提供的 summary 数值，不检查 payload。于是 `-1.0 m` 的 node depth 可以被写入并把 attempt/simulation 发布为 completed。已有 `surface_flood` typed-frame validator 证明项目知道有限/非负规则，但当前 ResultRepository 没有生产调用点或 schema dispatch 与其绑定。
- **冻结契约：** 所有 frame 状态必须有限且非负，负 depth/volume 超过容差必须失败，禁止静默 clamp：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:343-360`。finalize 必须验证全部 frame + summary + quality：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:812-815`。
- **测试缺口：** `backend/tests/hydraulic/test_results_calibration.py:161-197,268-341` 只覆盖 canonical JSON、grid、hash 和正常 payload，没有负 depth/volume、未知 schema 或非数值状态。
- **诊断 D3：** 使用 `{'nodes': {'depth_m': [-1.0]}}` 调用真实 `prepare_frame` 与 `ResultRepository.finalize`，输出 `published_negative_depth=True attempt_status=published simulation_status=completed`。
- **建议修复：** 建立 `schema_version -> strict frame validator` 的不可变 registry；registration 前和 finalize 前均验证完整 payload（所有数值有限，depth/volume/area/storage 等按契约非负，未知字段/schema fail closed）。不得用 `max(0, value)` 修复。为 node/pipe/ponding 各状态类型增加负值、NaN/Inf、缺字段、重复 target 和未知 schema 测试。

### H3 — Courant 发布阈值错误地放宽到 1.0

- **严重度：high**
- **位置：** `backend/app/services/hydraulic/results.py:838-878`
- **问题：** `_validate_quality_report` 只在 `max_courant > 1.0` 时失败，因此 `0.5 < Courant <= 1.0` 仍可标记 `verified` 并发布。冻结数值质量契约要求内部时间步 `Courant <= 0.5`，超出应为 `NUMERICAL_STABILITY_LIMIT`。
- **冻结契约：** `docs/hydraulic-twin/PRD_SUPPLEMENT.md:360-362`。
- **代码证据：** 硬门禁写为 1.0：`backend/app/services/hydraulic/results.py:864-868`。
- **测试缺口：** finalize 测试只传 `max_courant=0.4`：`backend/tests/hydraulic/test_results_calibration.py:317-325`，没有 0.5 边界和 `0.5+epsilon` 拒绝。
- **诊断 D2：** `status=verified, max_courant=0.75` 未抛错，输出 `accepted_status=verified max_courant=0.75`。
- **建议修复：** 将硬上限改为 `0.5`，并对负数、NaN/Inf、缺失值按 solver/quality schema 作 fail-closed 处理；补 `0.5` 通过、`nextafter(0.5, 1)` 失败的结构化错误码测试。

### H4 — 合法的固定参数区间会让 random search 永不结束

- **严重度：high**
- **位置：** `backend/app/services/hydraulic/calibration.py:287-318,935-967`
- **问题：** `ParameterSpec` 明确允许 `maximum == minimum`。random generator 第一次产生固定值后，第二次仍产生同一 canonical key；`seen` 分支 `continue` 且不增加 `generated`，所以 `max_evaluations>1` 时无限循环。极窄到不足一个浮点 ULP 的区间也有同类风险。durable worker 调用该公开算法时会卡住 rate-limit/heartbeat 路径。
- **测试缺口：** `backend/tests/hydraulic/test_results_calibration.py:506-527` 只覆盖宽度 20 的随机区间和可复现性，没有固定值/不可区分区间。
- **诊断 D4：** `ParameterSpec('x', 1.0, 1.0)`、`max_evaluations=2` 取得第一个候选后，8 秒 watchdog 以 exit 124 终止，第二个候选始终不返回。
- **建议修复：** random search 要么在预检时要求每个可随机参数 `minimum < maximum`，要么对固定维度只生成一次并继续其他维度；对浮点空间耗尽/重复停滞设置有限重试和结构化错误。补固定区间、极窄区间和多维固定维度测试。

### L1 — `prepare_frame` 的错误信息声称整数校验，但实际接受 float

- **严重度：low**
- **位置：** `backend/app/services/hydraulic/results.py:138-160,248-289`
- **问题：** `FrameInput.__post_init__` 对 `frame_index` 只排除 bool 和负数，没有要求 `int`；`schema_version` 也只做大小比较。只要时间与 float index 对齐，`prepare_frame` 会返回 float index 的 `PreparedFrame`。repository 的 dense-index 检查通常会在后续挡住 0.5，但公开 prepare 层没有落实冻结的 INTEGER 契约，错误会延迟到更深层或被 ORM 强制转换。
- **诊断 D5：** `frame_index=0.5`、时间对齐 150 秒后输出 `accepted_frame_index=0.5 type=float`。
- **建议修复：** 对 `frame_index` 和 `schema_version` 显式执行非 bool 的 `isinstance(..., int)`；`expected_frame_count` 同样拒绝 bool/non-int。补 public DTO 边界测试。

## 缺测与未验证项

现有 13 个聚焦测试均通过，但至少缺少：

1. 固定 validation/test split、同一 paired cohort、至少 3 个实际 paired events，以及 baseline/candidate lineage 不一致拒绝。
2. auth/binding verified observation 的不可伪造准入与普通 synthetic 数据冒充 verified 的拒绝。
3. `max_courant=0.5` 边界和 `>0.5` 拒绝。
4. 负 depth/volume、NaN/Inf、未知 schema、重复/缺失 target 的 finalize 拒绝。
5. random search 固定值/极窄区间不会无限循环。
6. `frame_index`/`schema_version` 的严格整数输入。
7. live PostgreSQL/TimescaleDB 同事务、FK/check、原子 finalize 与 retention policy；本次未运行。
8. live Influx bucket/token/field；本次未运行。

## 最终判断

聚焦测试通过只能证明实现摘要列出的正常路径。真实代码仍可把不可比较的 baseline/candidate 事件判为 AC-5 PASS，发布负水力状态并放过超过冻结 `0.5` 上限的 Courant；固定参数区间还会使随机搜索无限循环。这些问题直接影响结果可信度、数值安全或 durable worker 可用性，因此 `approved=false`。
