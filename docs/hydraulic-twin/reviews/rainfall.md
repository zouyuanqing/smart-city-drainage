# 降雨情景与雨源独立审查

- 审查日期：2026-09-25
- 结论：**不批准（4 high，3 medium）**
- 审查方式：只读核对冻结契约、真实实现、聚焦测试和实现摘要；未修改实现或测试。

## 范围与契约依据

本次按任务给出的冻结规格审查以下交付：1–100 年设计雨型、最长 72h 自定义校验、可复现随机雨型、`RainfallSource`、参数校验和情景复制语义。实际读取并核对了：

- `docs/hydraulic-twin/SOURCE_PRD.md:19`：重现期、1/5/15min 步长、72h、自定义/复制、仅 synthetic 源。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:294-301`：允许重现期词汇、样本数、source/version/seed/algorithm/hash、不可原地修改及提交前校验。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1300-1312`：`RainfallSource` 形状和 `RainfallSeries` 必含元数据。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:598-620`：scenario 表结构及复制 `root/seed/version+1` 语义。
- `backend/app/services/hydraulic/rainfall.py`、`backend/tests/hydraulic/test_rainfall.py`、`docs/hydraulic-twin/implementation/rainfall.md` 的真实内容。

## 实际执行的检查

### 聚焦测试（任务给出的测试文件）

执行命令：

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest tests/hydraulic/test_rainfall.py -q
```

实际输出：

```text
...........................                                              [100%]
27 passed, 1 warning in 0.04s
```

警告来自 Pydantic `model_version` protected namespace。聚焦测试通过，但未覆盖下列失败边界。

按任务要求未重复全量 hydraulic 或全后端门禁；实现摘要中的历史全量结果不作为本次通过证据。

## 定向诊断命令与实际输出

以下命令均在 `smart-city-drainage/backend` 执行。

### D1 — 非整除尾段积分

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
from datetime import datetime, timezone
from app.services.hydraulic.rainfall import generate_custom_rainfall
s = datetime(2026, 1, 1, tzinfo=timezone.utc)
p = generate_custom_rainfall([0.0, 4.0, 8.0], start_at=s, duration_seconds=601, step_seconds=300)
expected = (4.0 * 300 + 8.0 * 1) / 3600
print("partial_depth", p.total_depth_mm, "expected_including_last_partial", expected, "values", p.intensity_mm_h)
PY
```

```text
partial_depth 0.3333333333 expected_including_last_partial 0.33555555555555555 values (0.0, 4.0, 8.0)
```

### D2 — 单区间设计雨型严格单调性

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
from datetime import datetime, timezone
from app.services.hydraulic.rainfall import ALLOWED_RETURN_PERIODS, generate_design_rainfall
s = datetime(2026, 1, 1, tzinfo=timezone.utc)
series = [generate_design_rainfall(p, start_at=s, duration_seconds=60, step_seconds=60) for p in ALLOWED_RETURN_PERIODS]
print("depths", [x.total_depth_mm for x in series])
print("peaks", [max(x.intensity_mm_h) for x in series])
PY
```

```text
depths [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
peaks [20.0, 40.0, 60.0, 100.0, 200.0, 400.0, 1000.0, 2000.0]
```

### D3 — custom scenario schema round-trip

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
import uuid
from datetime import datetime, timezone
from app.schemas.hydraulic_schemas import RainfallScenarioCreate
from app.services.hydraulic.rainfall import generate_custom_rainfall, materialize_scenario
s = datetime(2026, 1, 1, tzinfo=timezone.utc)
series = generate_custom_rainfall([0.0, 1.0], start_at=s, duration_seconds=300, step_seconds=300, return_period_years="custom")
scenario = materialize_scenario(series, network_id=uuid.UUID(int=1))
payload = {"network_id": scenario.network_id, "name": scenario.name, "kind": scenario.kind, "return_period_years": scenario.return_period_years, "step_seconds": scenario.step_seconds, "duration_seconds": scenario.duration_seconds, "source_type": scenario.source_type, "algorithm": scenario.algorithm, "random_seed": scenario.random_seed, "points": tuple({"point_index": p.point_index, "valid_at": p.valid_at, "intensity_mm_h": p.intensity_mm_h} for p in scenario.points), "content_sha256": scenario.content_sha256}
try:
    RainfallScenarioCreate(**payload)
except Exception as exc:
    print(str(exc))
PY
```

```text
1 validation error for RainfallScenarioCreate
return_period_years
  Input should be a valid integer, unable to parse string as an integer [type=int_parsing, input_value='custom', input_type=str]
    For further information visit https://errors.pydantic.dev/2.6/v/int_parsing
```

### D4 — source/algorithm provenance

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
from dataclasses import replace
from datetime import datetime, timezone
from app.services.hydraulic.rainfall import RainfallRequest, generate_random_rainfall, materialize_rainfall, materialize_scenario
s = datetime(2026, 1, 1, tzinfo=timezone.utc)
base = {"start_at": s, "duration_seconds": 300, "step_seconds": 60, "kind": "random", "random_seed": 7}
a = materialize_rainfall(RainfallRequest(**base, algorithm="algorithm-a"))
b = materialize_rainfall(RainfallRequest(**base, algorithm="algorithm-b"))
print("arbitrary_algorithms", a.intensity_mm_h == b.intensity_mm_h, a.algorithm, b.algorithm)
series = generate_random_rainfall(start_at=s, duration_seconds=300, step_seconds=60, seed=7)
external = replace(series, source_id="external", source_version="external-v9", content_sha256="")
scenario = materialize_scenario(external)
print("external_wrapped", scenario.source_type, scenario.source_id)
PY
```

```text
arbitrary_algorithms True algorithm-a algorithm-b
external_wrapped synthetic external
```

### D5 — falsey source 选择

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
from datetime import datetime, timezone
from app.services.hydraulic.rainfall import RainfallRequest, materialize_rainfall
class FalseySource:
    source_id = "test-double"
    version = "1"
    def __bool__(self): return False
    def materialize(self, request): return "TEST_DOUBLE_CALLED"
result = materialize_rainfall(RainfallRequest(start_at=datetime(2026, 1, 1, tzinfo=timezone.utc), duration_seconds=300, step_seconds=60, kind="random"), source=FalseySource())
print(type(result).__name__, getattr(result, "source_id", None))
PY
```

```text
RainfallSeries synthetic
```

### D6 — 省略 `duration_seconds` 的直接构造

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
from datetime import datetime, timedelta, timezone
from app.services.hydraulic.rainfall import RainfallSeries
s = datetime(2026, 1, 1, tzinfo=timezone.utc)
try:
    RainfallSeries(start_at=s, end_at=s + timedelta(seconds=60), native_step_seconds=60, internal_step_seconds=60, intensity_mm_h=(1.0, 2.0))
except Exception as exc:
    print(type(exc).__name__, repr(str(exc)))
PY
```

```text
AssertionError ''
```

### D7 — Scenario metadata/空名称

```text
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
from datetime import datetime, timezone
from app.services.hydraulic.rainfall import RainfallScenario, generate_custom_rainfall, generate_design_rainfall, materialize_scenario
s = datetime(2026, 1, 1, tzinfo=timezone.utc)
design = generate_design_rainfall(50, start_at=s, duration_seconds=300, step_seconds=60)
scenario = RainfallScenario(series=design)
print("direct_scenario", scenario.kind, scenario.return_period_years, "series", design.kind, design.return_period_years)
custom = generate_custom_rainfall([0.0, 1.0], start_at=s, duration_seconds=300, step_seconds=300)
blank = materialize_scenario(custom, name="")
print("blank_name", repr(blank.name))
PY
```

```text
direct_scenario synthetic None series design 50
blank_name 'custom rainfall'
```

## Findings

### H1 — 非整除 duration 的尾段被总量积分遗漏

- **严重度：high**
- **位置：** `backend/app/services/hydraulic/rainfall.py:504-507,900-909`
- **问题：** `RainfallRequest` 明确允许非整除持续时间并保留最终 partial interval，`RainfallSeries.total_depth_mm` 的 docstring 也声称包含 partial interval；但实现固定遍历 `intensity_mm_h[:-1]`。当最后一个采样点位于 `end_at` 之前时，它恰好是尾段 partial interval 的起点，却被无条件丢弃。`catchment_rainfall_mm` 又直接基于该错误总量计算，因此错误会传播到汇水区雨量。
- **证据：**
  - 允许 partial interval 的实现说明：`backend/app/services/hydraulic/rainfall.py:504-507`。
  - 错误积分循环：`backend/app/services/hydraulic/rainfall.py:900-909`。
  - 实现摘要也明确声称按尾段积分：`docs/hydraulic-twin/implementation/rainfall.md:95`。
  - 非整除测试只断言样本数/时间戳，未断言总量：`backend/tests/hydraulic/test_rainfall.py:99-113`；总量测试只使用整除的 600s/300s：`backend/tests/hydraulic/test_rainfall.py:260-278`。
  - 定向诊断 D1：`duration=601, step=300, values=[0,4,8]` 得到 `0.3333333333`，包含最后 1s×8mm/h 后应为 `0.33555555555555555`。
- **建议修复：** 按每个样本到 `min(next_sample, end_at)` 的实际区间积分；仅当采样点正好等于 `end_at` 且剩余时长为 0 时排除。补充非整除、duration<step 和汇水区分配回归测试。

### H2 — 合法单区间设计雨型总量恒为零，破坏重现期严格单调性

- **严重度：high**
- **位置：** `backend/app/services/hydraulic/rainfall.py:993-1006`
- **问题：** `_design_intensities` 使用 `peak=max(step, duration*0.4)`。当合法输入 `duration_seconds == step_seconds`（例如 60/60）时，峰值恰好落在终点样本；H1 所示积分规则又排除终点样本。因此所有 1–100 年设计雨型虽然终点峰值不同，累计深度却全部为 0。当前测试仅在 3600s/300s 网格验证严格单调，未覆盖最小合法雨型。
- **证据：**
  - 设计 profile 的峰值/终点逻辑：`backend/app/services/hydraulic/rainfall.py:993-1006`。
  - 测试只覆盖 3600s/300s：`backend/tests/hydraulic/test_rainfall.py:30-50`。
  - 任务给出的 `testHint` 明确包含“重现期单调性”；冻结规格允许所有正持续时间搭配冻结步长：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:296`。
  - 定向诊断 D2：八个允许重现期的 `depths` 全为 `0.0`，而 `peaks` 为 `20..2000`，总量不严格单调。
- **建议修复：** 明确点值语义并让设计 profile 在每个可表示网格上都产生正、可积分的单区间雨量；至少对所有允许的短 duration/步长组合断言 `total_depth(period+1) > total_depth(period)`。若某组合无法表达，应在请求入口明确拒绝，而不是静默生成零雨量。

### H3 — `custom` 重现期输出无法通过现有 API schema/ORM 持久化

- **严重度：high**
- **位置：** `backend/app/services/hydraulic/rainfall.py:242-267,625-632`；`backend/tests/hydraulic/test_rainfall.py:281-291`
- **问题：** 冻结补充语义把 `custom` 列入允许词汇，模块也有意接受并测试 `return_period_years="custom"`。但真实 `RainfallScenarioCreate` 只接受整数枚举或 `None`，ORM 列也是 `Integer` 且约束仅允许 NULL/数值重现期。因而模块正式支持并产出的 custom 标签无法进入现有持久化契约。
- **证据：**
  - 冻结允许 `custom`：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:295-296`。
  - 模块接受 `"custom"`：`backend/app/services/hydraulic/rainfall.py:242-267`。
  - 测试把该行为固定为期望：`backend/tests/hydraulic/test_rainfall.py:281-291`。
  - Pydantic schema：`backend/app/schemas/hydraulic_schemas.py:663-689`。
  - ORM 整数列及 CHECK：`backend/app/models/hydraulic_models.py:753-805`。
  - 定向诊断 D3 将模块生成的 custom scenario 送入 `RainfallScenarioCreate`，得到 `ValidationError: Input should be a valid integer, unable to parse string as an integer, input_value='custom'`。
- **建议修复：** 由契约 owner 明确唯一持久化表示：若必须保存字面量 `custom`，同步 schema、ORM、迁移和 API；若数据库继续用 NULL 表示 custom，则模块序列化应输出 NULL，并调整模块测试。需要增加模块→schema→ORM 的 round-trip 契约测试。

### H4 — v1 source/algorithm provenance 可伪造，非 synthetic series 会被包装成 synthetic scenario

- **严重度：high**
- **位置：** `backend/app/services/hydraulic/rainfall.py:549-565,809-827,1358-1385`
- **问题：** `RainfallRequest.algorithm` 只校验非空，不校验其是否与 pattern 的冻结算法一致；`RainfallSeries` 对 `source_id/source_version/algorithm` 也只校验非空。随后 `RainfallScenario` 只检查调用者提供的 `source_type == synthetic`，却无条件把 series 的任意 `source_id` 写入 scenario。结果是相同 synthetic 样本可被标成不同算法，外部 source 也可得到 `source_type=synthetic, source_id=external` 的矛盾对象，破坏“仅内置 synthetic 源”和可审计重放。
- **证据：**
  - v1 仅 synthetic：`docs/hydraulic-twin/SOURCE_PRD.md:19`；固定 source/version/seed/algorithm/hash：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:297-298`。
  - 算法仅做长度/非空校验：`backend/app/services/hydraulic/rainfall.py:549-565`。
  - series provenance 仅做非空校验：`backend/app/services/hydraulic/rainfall.py:809-827`。
  - scenario 检查 source_type 后从 series 复制 source_id：`backend/app/services/hydraulic/rainfall.py:1358-1385`。
  - 定向诊断 D4：算法 `algorithm-a`/`algorithm-b` 生成完全相同样本；external series 被包装为 `synthetic external`。
- **建议修复：** 建立并校验 `(source_id, source_version, pattern, algorithm)` 支持矩阵；scenario 必须要求 series provenance 与 v1 synthetic 完全一致。增加算法/source 篡改拒绝测试。

### M1 — 显式注入的 falsey `RainfallSource` 会被静默替换为内置源

- **严重度：medium**
- **位置：** `backend/app/services/hydraulic/rainfall.py:1265-1275`
- **问题：** `selected = source or SYNTHETIC_RAINFALL_SOURCE` 按 truthiness 而非 `None` 选择 source。满足 Protocol 但 `bool(source) is False` 的合法测试替身/实现不会被调用，调用者却会得到看似成功的 synthetic 结果，破坏 source 抽象的确定性。
- **证据：** 定向诊断 D5 传入 `materialize()` 明确返回 `TEST_DOUBLE_CALLED` 的 falsey source，实际输出类型为 `RainfallSeries`、source_id 为 `synthetic`。
- **建议修复：** 使用 `source if source is not None else ...`；随后校验返回对象确为 `RainfallSeries`，并验证其 source identity/version。补一个 falsey Protocol 实现测试。

### M2 — `RainfallSeries(duration_seconds=None)` 的公开推导路径以裸 `AssertionError` 崩溃

- **严重度：medium**
- **位置：** `backend/app/services/hydraulic/rainfall.py:722-738,829-848,878-880`
- **问题：** dataclass 允许 `duration_seconds=None`，`__post_init__` 也计算局部 `duration`，但没有把它写回对象；随后构造 canonical hash 时调用 `series.duration`，该 property 断言字段非 None。内置 source 总会显式传值，所以聚焦测试未触发；自定义 `RainfallSource` 直接构造协议返回对象时会失败。
- **证据：** 定向诊断 D6 在提供合法 start/end、native/internal step 和两个样本、但省略 `duration_seconds` 时得到 `AssertionError`。
- **建议修复：** 在计算 payload/hash 前用 `object.__setattr__` 写回规范化 duration；或者让 duration 成为必填字段并在入口抛出结构化 `RainfallValidationError`。补直接构造 `RainfallSeries` 的测试。

### M3 — Scenario 构造未校验 series 一致性，空名称被静默改写

- **严重度：medium**
- **位置：** `backend/app/services/hydraulic/rainfall.py:1295-1312,1315-1387,1492-1518`
- **问题：** 直接构造 `RainfallScenario(series=design_series)` 时，默认 `kind=synthetic, return_period_years=None` 与 series 的 `design/50` 不一致仍可通过；scenario 校验的是独立 metadata，而不是二者一致性。另一个入口 `materialize_scenario(..., name="")` 使用 `name or default_name`，把明确无效的空名称静默替换成默认名称，而不是执行参数校验。
- **证据：** 定向诊断 D7 输出 `direct_scenario synthetic None series design 50` 和 `blank_name 'custom rainfall'`。
- **建议修复：** scenario 的 kind/return period/seed/source/algorithm 必须从 series 派生或逐项一致性校验；区分 `name is None` 与 `name == ""`，空字符串应抛结构化验证错误。补直接构造和空名称测试。

## 缺测汇总

现有 27 个测试均通过，但缺少以下会捕获实际问题的边界：

1. 非整除 duration 的 `total_depth_mm` 与 `catchment_rainfall_mm`。
2. `duration_seconds <= step_seconds` 的设计/随机雨型及重现期严格单调性。
3. `duration_seconds=None` 的直接 `RainfallSeries` 构造。
4. source/algorithm 篡改与外部 source 拒绝。
5. falsey `RainfallSource` 选择。
6. custom scenario 到现有 schema/ORM 的 round-trip。
7. scenario metadata 与 series 一致性、空名称拒绝。
8. `ALLOWED_STEP_SECONDS` 中 900s 的显式正向用例（当前只间接覆盖 60s/300s）。

## 最终判断

聚焦测试通过不足以抵消 H1–H4。合法非整除/单区间雨型会产生错误总量，`custom` 场景无法持久化，且 source/algorithm provenance 可伪造；这些均属于核心契约或可审计重放风险，因此 `approved=false`。
