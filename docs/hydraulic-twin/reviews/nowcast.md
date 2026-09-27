# Nowcast 调度与初始状态同化独立审查

- 模块：`nowcast`
- 审查日期：2026-09-25
- 结论：**不批准（5 high，3 medium）**
- 审查方式：只读核对任务给出的冻结规格、冻结 PRD/实施契约、真实实现、真实测试、Settings 与 ORM 边界，并运行目标 Python 3.11 聚焦测试和小型定向诊断；未修改生产代码或测试。
- 决策：`approved = false`。存在会允许 mock/无来源观测进入正式初始状态、允许无率定模型调度、错误标记 dry、破坏状态 hash，以及无法形成冻结 run identity 的高风险问题。

## 1. 审查范围与契约依据

本次按任务规格审查三个交付：默认 10 分钟调度、0–2h/5 分钟步长；使用率定模型和最近可信传感器初始化；同一任务/时隙去重并提供可测试时钟。同时用冻结契约检查生产边界：

- 源 PRD：默认 10 分钟 nowcast、0–2h/5 分钟步长：`docs/hydraulic-twin/SOURCE_PRD.md:20-24`；nowcast 到告警 3 分钟：`docs/hydraulic-twin/SOURCE_PRD.md:30-32`。
- 观测 trust 必须由 policy 决定；只有具备完整 source event identity、认证、有效 binding、`quality=good` 的 `external_verified` 可用于正式同化，mock/synthetic 不可用：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:106-118`。
- nowcast 默认 10/120/5/180，fake-clock 100 时隙：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:429-433`。
- 初始状态是独立 hash、绑定 source event refs，dry 必须为确定性全零 typed rows：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:302-357`。
- observation sample 只允许 `water_depth_m|flow_m3s`，正式同化只允许 verified/auth/binding/good：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:870-896`。
- nowcast run 必须冻结 network revision、target manifest、solver/rainfall/model calibration 和 flood/surcharge probability/rule 工件身份；调度前 policy 缺失时不创建 run：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:967-1105`。
- durable job 共用唯一状态机，nowcast 固定 deadline 为 `scheduled_for + 180s`：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1376-1405`。
- lead time 以 `issued_at` 为基准，不能以请求创建时间代替：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:210`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1101-1104`。

真实实现与测试已读取：

- `backend/app/services/hydraulic/nowcast.py`
- `backend/tests/hydraulic/test_nowcast.py`
- `docs/hydraulic-twin/implementation/nowcast.md`
- `backend/app/core/config.py:295-353,387-415`
- `backend/app/models/hydraulic_models.py:964-1008,2199-2411`

仓库没有 `docs/adr/` 或 `_project_specs/`，工作区也没有 `.codegraph/` 索引；因此本轮使用真实文件读取和精确定位搜索，没有使用 CodeGraph，也没有臆造缺失 ADR。

## 2. 实际执行的检查

### 2.1 目标 Python 3.11 聚焦测试

精确命令：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && /c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest tests/hydraulic/test_nowcast.py -q --tb=short
```

实际输出：

```text
............                                                             [100%]
============================== warnings summary ===============================
..\..\..\..\..\AppData\Local\uv\cache\archive-v0\41x0ZNIPAy35J9c5\Lib\site-packages\pydantic\_internal\_fields.py:151
  C:\Users\zyq\AppData\Local\uv\cache\archive-v0\41x0ZNIPAy35J9c5\Lib\site-packages\pydantic\_internal\_fields.py:151: UserWarning: Field "model_version" has conflict with protected namespace "model_".
  
    warnings.warn(

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
12 passed, 1 warning in 0.05s
```

补充使用当前默认解释器执行同一测试文件：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && python -m pytest tests/hydraulic/test_nowcast.py -q --tb=short
```

输出为 `12 passed, 288 warnings in 0.04s`。目标 3.11 与当前 3.14 的聚焦测试都通过，但下列边界未被测试覆盖。

### 2.2 目标版本边界诊断

精确命令：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && /c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from app.services.hydraulic.nowcast import (
    CalibratedModel, InitialState, InitialStateAssimilator, ManualClock,
    NowcastConfig, NowcastScheduler, SensorReading,
)

UTC = timezone.utc
START = datetime(2026, 9, 25, tzinfo=UTC)
NETWORK = "network-demo"
assimilator = InitialStateAssimilator(clock=ManualClock(START))

state = assimilator.assimilate(
    {"node_ids": ["N-001"]},
    [{"target_id": "N-001", "observed_at": START, "value": 0.2}],
    as_of=START,
)
print("mapping_without_trust_or_event=ACCEPTED", {
    "source": state.node_states["N-001"]["source"],
    "trust": state.node_states["N-001"]["trust_level"],
    "source_event_ids": state.source_event_ids,
})

model = CalibratedModel(
    model_id="calibrated", model_version="1", calibration_id="cal-1",
    initial_state={"N-001": {"depth_m": 0.03}},
)
state = assimilator.assimilate({"node_ids": ["N-001"]}, [], as_of=START, model=model)
print("nonzero_model_without_sensor", {
    "kind": state.kind,
    "depth_m": state.node_states["N-001"]["depth_m"],
    "missing_targets": state.missing_targets,
})

state = assimilator.assimilate(
    {"node_ids": ["N-001"]}, [], as_of=START,
    model={"model_id": "m", "calibrated": False, "initial_state": {"N-001": {"depth_m": 0.1}}},
)
print("uncalibrated_mapping=ACCEPTED", {
    "kind": state.kind,
    "depth_m": state.node_states["N-001"]["depth_m"],
})
outcome = NowcastScheduler(
    clock=ManualClock(START), network={"node_ids": ["N-001"]}
).run_due(NETWORK, now=START)
print("missing_model_schedule=ACCEPTED", {
    "created": outcome.created,
    "model": outcome.model,
})

mutable_state = InitialState(
    kind="warm_state", node_states={"N-001": {"depth_m": 0.1}}
)
before = mutable_state.initial_state_content_sha256
mutable_state.node_states["N-001"]["depth_m"] = 99.0
after = mutable_state.initial_state_content_sha256
print("nested_state_mutation", {
    "hash_unchanged": before == after,
    "current_depth_m": mutable_state.node_states["N-001"]["depth_m"],
})

state = assimilator.assimilate(
    {"node_ids": ["N-001"]},
    [SensorReading("N-001", START, -0.5, source_event_id="event-1")],
    as_of=START,
    model=model,
)
print("negative_depth=ACCEPTED", {
    "depth_m": state.node_states["N-001"]["depth_m"],
})

async def async_duplicate_probe():
    observation_calls = 0
    model_calls = 0
    async def observation_provider(network_id, as_of):
        nonlocal observation_calls
        observation_calls += 1
        return ()
    async def model_provider(network_id, as_of):
        nonlocal model_calls
        model_calls += 1
        return model
    scheduler = NowcastScheduler(
        NowcastConfig(), clock=ManualClock(START),
        network={"node_ids": ["N-001"]},
        model_provider=model_provider, observation_provider=observation_provider,
    )
    first = await scheduler.run_once_async(NETWORK, now=START)
    second = await scheduler.run_once_async(NETWORK, now=START)
    print("async_duplicate_provider_calls", {
        "first_created": first.created,
        "second_created": second.created,
        "same_task": first.task_id == second.task_id,
        "observation_calls": observation_calls,
        "model_calls": model_calls,
    })
asyncio.run(async_duplicate_probe())

clock = ManualClock(START)
provider_calls = 0
def provider(network_id, as_of):
    global provider_calls
    provider_calls += 1
    return model
scheduler = NowcastScheduler(
    NowcastConfig(), clock=clock, model_provider=provider,
    network={"node_ids": ["N-001"]},
)
created = []
last = None
for index in range(100):
    last = scheduler.run_due(NETWORK, now=clock.now())
    created.append(last.created)
    if index == 99:
        with ThreadPoolExecutor(max_workers=16) as pool:
            outcomes = list(pool.map(
                lambda _: scheduler.run_due(NETWORK, now=clock.now()), range(32)
            ))
    clock.advance(minutes=10)
print("virtual_100_slots", {
    "created": sum(created),
    "stored": len(scheduler.list_runs(NETWORK)),
    "provider_calls": provider_calls,
    "concurrent_same_id": len({item.task_id for item in outcomes}),
    "concurrent_created_count": sum(item.created for item in outcomes),
})
PY
```

实际输出：

```text
mapping_without_trust_or_event=ACCEPTED {'source': 'sensor', 'trust': 'external_verified', 'source_event_ids': ()}
nonzero_model_without_sensor {'kind': 'dry', 'depth_m': 0.03, 'missing_targets': ('N-001',)}
uncalibrated_mapping=ACCEPTED {'kind': 'dry', 'depth_m': 0.1}
missing_model_schedule=ACCEPTED {'created': True, 'model': None}
nested_state_mutation {'hash_unchanged': True, 'current_depth_m': 99.0}
negative_depth=ACCEPTED {'depth_m': -0.5}
async_duplicate_provider_calls {'first_created': True, 'second_created': False, 'same_task': True, 'observation_calls': 2, 'model_calls': 2}
virtual_100_slots {'created': 100, 'stored': 100, 'provider_calls': 100, 'concurrent_same_id': 1, 'concurrent_created_count': 0}
```

最后一行的 100 时隙只证明内存 scheduler 的创建与同槽并发幂等，不包含冻结 G5 要求的 durable 状态、retry、stale、policy、outbox 或 30 个真实周期。

### 2.3 mock 来源 fail-open 诊断

精确命令：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && /c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
from datetime import datetime, timezone
from app.services.hydraulic.nowcast import InitialStateAssimilator, ManualClock
start = datetime(2026, 9, 25, tzinfo=timezone.utc)
state = InitialStateAssimilator(clock=ManualClock(start)).assimilate(
    {"node_ids": ["N-001"]},
    [{"source": "mock", "target_id": "N-001", "observed_at": start, "value": 0.2, "quality": "good"}],
    as_of=start,
)
print({"source": state.node_states["N-001"]["source"], "trust_level": state.node_states["N-001"]["trust_level"], "source_event_ids": state.source_event_ids})
PY
```

实际输出：

```text
{'source': 'sensor', 'trust_level': 'external_verified', 'source_event_ids': ()}
```

### 2.4 调度请求与冻结 run identity 字段对比

精确命令：

```text
cd "/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && /c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B - <<'PY'
from dataclasses import fields
from app.services.hydraulic.nowcast import ScheduledNowcast
actual = {field.name for field in fields(ScheduledNowcast)}
required = {
    "network_revision_id", "target_manifest_id", "target_manifest_sha256",
    "rainfall_scenario_id", "solver_id", "solver_version",
    "flood_forecast_probability_calibration_id",
    "flood_forecast_probability_sha256",
    "pipe_surcharge_probability_calibration_id",
    "pipe_surcharge_probability_sha256",
    "flood_forecast_rule_id", "flood_forecast_rule_sha256",
    "pipe_surcharge_rule_id", "pipe_surcharge_rule_sha256",
    "config_hash",
}
print("scheduled_nowcast_fields", sorted(actual))
print("frozen_run_fields_missing", sorted(required - actual))
PY
```

实际输出：

```text
scheduled_nowcast_fields ['config', 'created_at', 'deadline_at', 'forecast_origin_at', 'initial_state', 'model', 'network_id', 'request_fingerprint', 'scheduled_for', 'task_id']
frozen_run_fields_missing ['config_hash', 'flood_forecast_probability_calibration_id', 'flood_forecast_probability_sha256', 'flood_forecast_rule_id', 'flood_forecast_rule_sha256', 'network_revision_id', 'pipe_surcharge_probability_calibration_id', 'pipe_surcharge_probability_sha256', 'pipe_surcharge_rule_id', 'pipe_surcharge_rule_sha256', 'rainfall_scenario_id', 'solver_id', 'solver_version', 'target_manifest_id', 'target_manifest_sha256']
```

## 3. High findings

### H1 — 无 trust/source identity 的观测会 fail-open 为正式传感器状态，mock 也能进入同化

- **位置：** `backend/app/services/hydraulic/nowcast.py:405-415,492-499,750-797,1096-1107`
- **问题：** `SensorReading` 默认 `trust_level="external_verified"`、`auth_verified=True`、`binding_verified=True`，同时 `source_event_id` 可为空；`is_formal` 不要求 source event identity。mapping 适配器对缺字段的 payload 也默认 verified/authenticated/bound，并完全忽略 `source="mock"`。因此任何良构的旧 Influx 点、mock 点或无绑定点，只要没有显式 untrusted 字段，就会覆盖率定模型基线。
- **证据：**
  - 冻结 trust 边界：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:106-118`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:870-896`。
  - 默认信任与缺失 event ID：`backend/app/services/hydraulic/nowcast.py:405-415,492-499`。
  - mapping fail-open：`backend/app/services/hydraulic/nowcast.py:768-784`。
  - 当前测试用没有 `source_event_id` 的默认 `SensorReading` 并期望进入正式状态：`backend/tests/hydraulic/test_nowcast.py:132-172,175-192`。
  - 定向诊断：普通 mapping 得到 `external_verified`；`source="mock"` 得到 `source='sensor', trust_level='external_verified', source_event_ids=()`。
- **影响：** synthetic/mock/无事件身份或无真实 binding 的数据可污染正式 nowcast 初始状态，审计结果却显示 verified sensor，违反同化事实源边界。
- **修复要求：** 取消 verified/auth/binding 的安全默认值；mapping 缺 trust policy 结果时必须 fail closed 为 `external_unverified`。正式同化必须同时验证 `external_verified`、auth、binding、good quality、source event identity、network revision/target mapping/version 和适用 policy；缺任一项拒绝。补 mock、legacy Influx、缺 event ID、过期 policy、跨 revision binding 的负向测试。

### H2 — “率定模型”不是调度前置条件；显式 `calibrated=False` 和完全无模型都能创建 run

- **位置：** `backend/app/services/hydraulic/nowcast.py:507-565,1147-1152,1454-1456,1575-1587,1618-1623`
- **问题：** `CalibratedModel.calibrated` 默认就是 `True`，calibration/model calibration/parameter set identity 均可为空；检查只覆盖 `CalibratedModel` 实例且只看布尔字段。mapping/任意对象即使明确 `calibrated=False` 也不会被拒绝。model provider 返回 `None` 时 scheduler 回退到 `self.model`，最终允许 `model=None` 继续创建任务。
- **证据：**
  - 任务规格明确要求“使用率定模型”。
  - `CalibratedModel` 的可选/默认校准身份：`backend/app/services/hydraulic/nowcast.py:515-522`。
  - 校准检查只判断具体 dataclass：`backend/app/services/hydraulic/nowcast.py:1147-1152,1618-1623`。
  - provider 缺失/`None` 无 fail-closed：`backend/app/services/hydraulic/nowcast.py:1575-1587`。
  - 定向诊断：显式 `calibrated=False` mapping 被接受；无 model 的 scheduler 返回 `created=True, model=None`。
- **影响：** 所谓“使用率定模型初始化”可以被调用方绕过；默认参数、未率定模型或未知模型实现均可生成看似正式的 nowcast 请求。
- **修复要求：** 调度前解析一个闭合、版本化、不可变的模型工件，强制包含 model/parameter/calibration ID、hash、适用 network/revision 和有效期；只接受明确验证为 calibrated 的类型。缺模型、未知类型、过期工件或 `calibrated=False` 均在创建 run 前失败。补默认 scheduler、mapping false、对象无身份、provider 返回 `None` 的测试。

### H3 — 仅有非零率定模型基线时仍标记为 `dry`，生成自相矛盾的初始状态

- **位置：** `backend/app/services/hydraulic/nowcast.py:1187-1241`
- **问题：** `kind` 仅取决于是否选中任何传感器：`warm_state if selected else "dry"`。当没有传感器但率定模型给每个 target 提供非零 baseline 时，状态仍标成 `dry`，同时保留 `missing_targets` 和非零值。现有测试反而把该行为固定为期望。
- **证据：**
  - 冻结契约规定 dry 为确定性全零 typed rows，warm state 完整覆盖对象：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:339-357`。
  - 错误判定：`backend/app/services/hydraulic/nowcast.py:1230-1233`。
  - 测试明确断言非零模型基线仍为 dry：`backend/tests/hydraulic/test_nowcast.py:195-206`。
  - 定向诊断：`kind='dry', depth_m=0.03, missing_targets=('N-001',)`。
- **影响：** 下游若按 `dry` 语义生成全零 snapshot、跳过观测来源或拒绝非零 warm state，会丢失率定模型基线；同一对象同时声称 dry 与非零，无法满足持久化/重放语义。
- **修复要求：** `dry` 只能由无传感器、无非零模型 baseline、完整全零状态推导；模型 baseline 参与时必须生成有明确 model source/as-of 的 warm state。校验 kind、observed/source metadata 与每个 state 值一致；更新错误测试并补全零/非零 model baseline 矩阵。

### H4 — `@dataclass(frozen=True)` 仍允许嵌套状态被修改，`content_sha256` 与真实状态立即失配

- **位置：** `backend/app/services/hydraulic/nowcast.py:571-628,1296-1298`
- **问题：** `InitialState` 只对顶层 mapping 做浅拷贝，内部 state 仍是调用方传入的可变 `dict`。构造后修改 `state.node_states[target]["depth_m"]` 不会重算 `content_sha256`；`ScheduledNowcast.request_fingerprint` 又冻结旧 hash，形成同一对象中“payload 已变、hash 未变”的状态。外部传入 `content_sha256` 也只校验长度，不校验 64 位小写 hex 或是否与内容一致；文件已定义 `_HASH_RE`，但未使用。
- **证据：**
  - 冻结 initial-state snapshot/hash 语义：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:339-357`。
  - 浅拷贝与一次性 hash：`backend/app/services/hydraulic/nowcast.py:596-628`。
  - 未使用的 hash 约束：`backend/app/services/hydraulic/nowcast.py:29`。
  - 定向诊断：depth 从 `0.1` 改为 `99.0` 后 `hash_unchanged=True`。
- **影响：** 调用方、provider 或后续 worker 可以改变已“冻结”状态而不改变 request fingerprint；重放、审计和持久化 hash 校验均可能指向不同 payload。
- **修复要求：** 使用 canonical JSON bytes 或递归不可变结构（tuple/frozen mapping）作为唯一状态表示，在构造时一次性规范化并计算 hash；拒绝外部不一致 hash，严格校验小写 64 hex。增加构造后尝试修改、非法 hash、深层嵌套和重复 fingerprint 的测试。

### H5 — `ScheduledNowcast` 缺少冻结 run 所需 lineage/policy 身份，无法形成可持久化或可审计的幂等请求

- **位置：** `backend/app/services/hydraulic/nowcast.py:1257-1325,1641-1655`
- **问题：** 调度输出和 fingerprint 只有 network、slot、部分周期配置、model identity 和 initial-state hash；缺少 `network_revision_id`、target manifest、rainfall、solver/version、forecast policy config hash，以及 flood/surcharge probability/rule ID/hash/validity。真实 `NowcastRun` ORM 把这些定义为非空且有复合 FK；同一 `(network_id, scheduled_for)` 下 revision/manifest/policy 变化也无法体现在 request fingerprint。实现文档宣称可注入生产 PostgreSQL repository，但当前 DTO 无法构造真实 row。
- **证据：**
  - 冻结 run identity/config hash：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1011-1016,1034-1090`。
  - 当前 DTO 字段：`backend/app/services/hydraulic/nowcast.py:1257-1270`。
  - 当前 fingerprint 输入：`backend/app/services/hydraulic/nowcast.py:1641-1655`。
  - ORM 非空 lineage/artifact 字段：`backend/app/models/hydraulic_models.py:2331-2395`。
  - 实现摘要的生产 repository 边界：`docs/hydraulic-twin/implementation/nowcast.md:7-10,69-74`。
  - 字段对比诊断列出 15 个冻结字段缺失。
- **影响：** 生产 repository 无法无损持久化该请求；幂等重放不能证明针对同一 revision/manifest/policy/solver，审计和 artifact fail-closed 也无法由 scheduler 保证。
- **修复要求：** 在创建 run 前冻结并验证完整 run config，至少包含 revision、target manifest、rainfall、solver/model/parameter/calibration、initial-state hash、policy config 和两类 probability/rule 工件身份；使用冻结 closed canonical object 生成 `config_hash`。若 policy/finalize 由其他 owner 负责，scheduler 必须接收其已验证、不可变的 preflight bundle，而不是创建缺少 lineage 的正式 slot。补 repository DTO→ORM round-trip、revision/manifest/policy 变化和 policy 缺失不创建 run 的测试。

## 4. Medium findings

### M1 — async 重复触发虽然复用 task ID，却会在去重前重复调用 model/sensor provider

- **位置：** `backend/app/services/hydraulic/nowcast.py:1708-1712,1777-1806`
- **问题：** 同步 `run_due()` 在 provider 前检查已有 slot；`run_once_async()` 为解析 awaitable，先调用两个 provider，再进入同步去重路径。这违反公开 docstring“重复触发不会再次调用 provider”，并会在 180 秒 SLO 内产生重复昂贵 I/O/模型加载或 provider 副作用。
- **证据：** 定向诊断中两次 async 调用返回同一 task、第二次 `created=False`，但 `observation_calls=2, model_calls=2`；当前测试只覆盖同步路径：`backend/tests/hydraulic/test_nowcast.py:62-107`。
- **修复要求：** 先通过 repository 原子 get/create 决定是否需要 factory，再在 factory 内解析 awaitable；或者提供真正异步、原子且不执行 factory 的 `get_or_create`。补 async 重复、invalid slot 和 provider 抛错测试。

### M2 — 同化未执行冻结 metric allowlist、单位一致性、target 类型和物理范围校验

- **位置：** `backend/app/services/hydraulic/nowcast.py:454-458,841-855,1047-1057,1205-1216`
- **问题：** DTO 只检查 finite；未知 metric 会被规范化后直接写入 state，metric 与 unit 冲突时也可能按错误比例转换。代码没有验证 observation metric 仅为 `water_depth_m|flow_m3s`、unit 是否匹配 canonical metric、metric 是否适用于 node/pipe/pond，也没有拒绝负 depth/volume 或不合理 fill ratio。
- **证据：**
  - 冻结 sample metric allowlist：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:875-896`。
  - 未知 metric 透传：`backend/app/services/hydraulic/nowcast.py:841-855`。
  - 状态直接覆盖：`backend/app/services/hydraulic/nowcast.py:1205-1216`。
  - 定向诊断：正式 reading 的 `depth_m=-0.5` 被接受。
- **修复要求：** 在构造 reading 和写 state 两层做闭合校验：metric allowlist、规范单位、target-type 兼容、数值范围和转换后有限性；不匹配时 fail closed 并记录结构化拒绝原因。补未知 metric、m/mm 冲突、L/s、负 depth、pipe/node metric 交叉测试。

### M3 — `lead_time_minutes()` 使用请求创建时间而不是最终 `issued_at`

- **位置：** `backend/app/services/hydraulic/nowcast.py:1304-1308`
- **问题：** 方法以 `created_at` 计算 lead time。`created_at` 是 scheduler 创建请求的时间，最终预报业务时间应为 finalize transaction 冻结的 `issued_at`；求解/排队延迟会被错误地从 lead time 中扣除。
- **证据：**
  - 冻结定义：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:210`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1101-1104`。
  - 当前计算：`backend/app/services/hydraulic/nowcast.py:1304-1308`。
  - 目标测试没有 `issued_at`/lead-time 边界。
- **修复要求：** 不要在 pre-issue request 上暴露最终 lead-time 语义；若保留兼容方法，应显式接收 finalize 后不可变的 `issued_at`，并按 `(expected_at-issued_at).total_seconds() // 60` 计算。补 issued_at 晚于 created_at、跨分钟和 past expected 的测试。

## 5. 缺测汇总

现有 12 个聚焦测试通过，但缺少以下会捕获实际问题的边界：

1. `source="mock"`、缺 trust policy、缺 source event ID、auth/binding false、跨 revision binding；
2. 显式 `calibrated=False`、无 model、未知 model 类型、model provider 返回 `None`、校准工件过期/hash 不符；
3. 非零模型 baseline 的 state kind、dry 全零不变量和完整 target 覆盖；
4. frozen DTO 的深层不可变性、hash 重算、非法外部 hash；
5. async 重复触发的 provider 调用次数，以及两个 scheduler/生产 repository 的原子并发；
6. revision/target manifest/policy/rule/probability 工件变化对 fingerprint 和持久化 round-trip 的影响；
7. 未知 metric、错误 unit、跨 target type metric、负 depth/volume；
8. `issued_at` 基准的 lead time；
9. 冻结 G5 的 100 fake slots 状态/retry/stale/policy/outbox harness，以及 30 个真实连续周期。

## 6. 积极项

- 默认 10/120/5/180 配置、25 个 inclusive horizon 点和非法配置拒绝测试通过：`backend/tests/hydraulic/test_nowcast.py:34-59`。
- 本轮额外执行 100 个虚拟时隙，内存 repository 创建 100 个不同 run；在第 100 个槽并发重复触发 32 次，只得到一个 task ID、0 个新 run，provider 总调用数仍为 100。
- 同步同槽重复、相邻槽创建、latest 观测、future/stale/unverified 拒绝、缺测 model baseline 和显式 `require_complete` 的现有测试均通过。
- 模块没有直接发 SSE、运行 solver 或写数据库，路径 ownership 与模块摘要一致。

这些积极项不足以抵消 H1–H5。

## 7. 未运行项

按任务要求未重复最终全量门禁：

- **未运行：** `tests/hydraulic` 全套。
- **未运行：** backend `tests` 全套。
- **未运行：** 官方 G4/G5/AC-4 harness、PostgreSQL durable repository、真实 Influx/device binding、policy artifact、outbox relay、30 真实周期和 180 秒 SLO。
- **未运行：** Black/isort/flake8/mypy、完整 backend 启动、Compose、migration。
- 实现摘要中的历史全量结果仅作为实现者材料引用，没有被本审查重新记为通过。

## 8. 最终判断

聚焦测试为绿，且基础虚拟时钟/同步幂等路径可工作；但真实同化边界目前 fail-open，率定模型不是强制前置条件，非零模型状态被错误标成 dry，冻结状态可被修改而 hash 不变，调度输出也无法携带冻结 run 所需的 revision/manifest/policy lineage。上述问题均位于本模块核心目标内，因此当前 **approved=false**。
