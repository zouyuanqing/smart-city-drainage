# 水力仿真实施契约：运动波/扩散波、SWMM、守恒、稳定性、率定与性能边界

> 状态：**SUPERSEDED / NON-NORMATIVE — 不得作为实现契约**。本文件已被 `../PRD_SUPPLEMENT.md` 与 `../IMPLEMENTATION_CONTRACT.md` 的整改候选 v2 取代，仅保留首轮领域审查轨迹。
>
> 2026-09-24 设计审查未批准 v1。规范实现必须使用：solver `auto|explicit` + 稳定 `solver_id`；强类型 NetworkSnapshot/PondCell/boundary/initial state；attempt-scoped frame staging + published pointer + fencing；`leased/running/completed` 状态；source ingest event identity；durable network jobs；revision-scoped target manifest。
>
> 以下旧段落中的“v1 不提供 auto”、`queued→running→succeeded`、`network_snapshot_id` 模糊模型、`PondCell.links: object`、frame 按 `(simulation_id,frame_index)` 唯一、旧文件路径/测试命令均被显式取代，禁止实现。

## 0. 证据口径与当前边界

### 0.1 本契约使用的证据

1. **任务给出的 PRD 摘要**要求：Windows 可运行的纯 Python 运动波/扩散波与简化地表积水降级求解器、EPA SWMM 优先适配、5 分钟帧、10 分钟超时、最多两次重试、并发 2、RMSE/NSE 率定，以及 5000 节点/10000 管段 24h 在 4 核纯 Python 上不超过 180s。
2. **当前没有水力实现。** 侦察报告记录了 `backend/app`、迁移、测试、前端和 Docker 中不存在网络、降雨、仿真、预测或 SWMM 实现，并明确把性能、求解器稳定性、任务恢复和率定列为未运行项（`docs/hydraulic-twin/recon/simulation.md:10-16,272-300`；`docs/hydraulic-twin/recon/quality.md:380-391`）。
3. **当前 CPU 依赖没有水力求解栈。** `requirements-cpu.txt` 有 NumPy，但没有 SciPy、SWMM、pyswmm、swmm-toolkit 或其他水力内核（`backend/requirements-cpu.txt:0-33`）。因此“纯 Python 180s”目前没有实现或基准证据。
4. **当前 Redis 不是持久任务队列。** 代码只有 publish/subscribe 与 get/set/delete，没有 claim、lease、ack、重试队列或死信（`backend/app/core/redis_client.py:82-99,150-164`）。当前 SSE 事件号和历史都在单进程内存中，队列满会丢弃事件并断连（`backend/app/services/sse_manager.py:32-43,90-120,142-189`）。
5. **当前 Compose 没有仿真 worker。** 有效服务只有 backend、postgres、redis、influxdb、nginx，且没有独立 worker 入口（`docker-compose.yml:7-177`；`docs/hydraulic-twin/recon/simulation.md:159-169`）。
6. **现有错误基线必须兼容。** 422 使用顶层 `detail` 和 `errors`，500 使用顶层 `detail` 和 `type`（`backend/app/main.py:313-335`）。水力接口只能做加法扩展，不能移除或改名这些字段。

### 0.2 本文不是实现完成证明

当前没有可运行的水力测试、SWMM 工件、纯 Python 求解器、性能 fixture、观测映射或率定数据集。因此本文定义的是**实现和验收合同**，不是 AC-3/AC-5/性能 NFR 已通过的声明。实际检查状态统一列在第 16 节。

## 1. 不可行点、明确决策与替代方案

### D-01：四个求解器身份必须拆开，禁止混称

必须使用以下稳定 `solver_id`：

| `solver_id` | 含义 | 保真级别 | 可用于产品定位 |
|---|---|---|---|
| `swmm_kw` | EPA SWMM 运动波路由 | `engine_exact` | 优先生产路径；仍需输入/率定验证 |
| `swmm_dw` | EPA SWMM 动态波路由 | `engine_exact` | 压力流、回水等优先生产路径；不等同于下述扩散波 |
| `py_mw` | 纯 Python 运动波管网求解 | `fast_screening` | 大规模快速推演、告警筛查、SWMM 不可用时的显式降级 |
| `py_dw` | 纯 Python 扩散波管网求解 | `reduced_fidelity` | 中小规模、缓坡、亚临界重力流；不得冒充 SWMM 动态波 |
| `py_ponding` | 简化地表积水单元 | `reduced_fidelity` | 与 `py_mw`/`py_dw` 组合的筛查模型；不得称为二维水动力模型 |

**硬决策：**

- `swmm_dw` 与 `py_dw` 不是同一个数值模型。结果、日志、API 和前端不得都只写“DW”。
- 所谓“纯 Python”定义为：没有 SWMM 或其他外部水力引擎、没有用户编译的 C/C++/Fortran/Cython/Numba 水力 kernel；固定版本 NumPy/SciPy 可用于数组和线性代数。SWMM adapter 不属于纯 Python fallback。
- v1 使用 `solver_selection=auto|explicit`；explicit 绑定稳定 `solver_id`，auto 在提交事务内 probe 并冻结实际 solver。运行后不得在同一 `simulation_id` 下静默切换。
- `py_*` 结果必须在响应、SSE、告警和导出中携带 `model_fidelity` 与 `is_authoritative=false`。权威生产告警默认只接受通过输入验证、质量门禁和率定批准的 `swmm_*` 配置；演示环境可显式开启降级告警。

### D-02：简化地表积水不能产生二维淹没范围或流速承诺

`py_ponding` 只能计算预离散 `PondCell` 的水位、蓄水量、溢流和显式单元交换。它不求解完整二维浅水方程，不承诺：

- 建筑阻挡、车辆、桥梁、涵洞等障碍物绕流；
- 街道/建筑级水深和流速；
- 动态淹没前锋到达时间；
- 导航或人员疏散所需 hydrodynamic truth。

若 UI 展示“淹没面”，纯 Python 模式必须显示“简化积水范围”；只有 EPA SWMM 2D 配置且质量门禁通过时，才可使用“二维地表积水”标签。

### D-03：20 MiB 必须解释为不可信输入上限，而不是结果工作区总上限

PRD 的“沙箱临时目录、20MB 上限”存在直接容量冲突：

- 24h、5 分钟帧、含初始帧共有 `24*60/5 + 1 = 289` 帧；
- 5000 节点和 10000 管段共有 `289 * 15000 = 4,335,000` 个对象-帧状态；
- 即使每个状态只保存 8 个 float32，原始二进制下限也约 132.4 MiB，尚未计索引、JSON/Parquet 开销、管径/流量等额外字段。

因此，**若 20 MiB 是 worker 工作目录总上限，FR-3 的全量 5 分钟帧要求在目标规模下不可同时满足。**

本契约采用以下可实现解释：

- `SIM_INPUT_MAX_BYTES = 20 * 1024 * 1024`：单个 INP/GeoJSON/归档上传的解压后总字节上限；所有 section、对象、token、坐标和压缩比也有限额。
- `SIM_WORKSPACE_MAX_BYTES = 2 GiB`：worker 私有临时工作区的总磁盘上限，用于 SWMM 进程文件和中间产物。
- 最终帧按输出间隔流式写 TimescaleDB，不在内存中累积全部 289 帧，也不把最终结果 JSON 写入沙箱。
- API 读取结果必须分页/按帧读取；禁止把全部帧返回到一个 JSON 响应。
- 若产品所有者坚持“整个沙箱始终不超过 20 MiB”，则必须改为“仅保存稀疏风险摘要/预渲染几何”，这会改变 FR-3，需先变更 PRD。

### D-04：纯 Python 180s 是待证明的性能承诺，不是已成立事实

当前无实现、无 benchmark、无目标机器数据（`docs/hydraulic-twin/recon/simulation.md:186-233,289-300`；`docs/hydraulic-twin/recon/quality.md:275-300`）。不得仅因“使用 Python”就在发布说明中声称满足。

本契约的硬边界：

- 180s 只约束**单任务在固定 4 物理核环境中的 worker 处理时间**，从 claim 后开始，到帧和摘要持久化完成为止；不包含队列等待。
- `py_mw` 是 5000/10000/24h/5 分钟帧规模 NFR 的唯一首发硬门禁。
- `py_dw` 与 `py_ponding` 没有 5000/10000 规模 180s 承诺；必须先跑 benchmark，再发布其规模上限。
- “并发 2”是容量/排队契约，不等于两项 5000 节点任务必须同时在 180s 内结束。若需要该吞吐 NFR，应另立“两任务同时完成”的验收。
- 如果 `py_mw` benchmark 失败，依次选择：(1) 仅对 SWMM 承诺全规模；(2) 改为固定版本 NumPy/SciPy 向量化/稀疏 kernel；(3) 使用预聚类/概化网络并把原模型作为校核；(4) 放宽时间或缩小帧范围。不得静默降低精度却不标记。

### D-05：AC-5 不能用 mock 数据完成

当前只有 Influx/mock 遥测与模拟回退，没有设备到节点/管段/汇水区映射、质量标志或真实观测同化（`docs/hydraulic-twin/recon/simulation.md:101-131`；`docs/hydraulic-twin/recon/backend-data.md:170-190`）。

因此：

- mock 观测可以跑 smoke test 和管线测试；
- `source="mock"` 的数据不得使 AC-5 标记为通过；
- 没有满足第 12 节样本门槛的真实观测时，率定请求必须返回 `INSUFFICIENT_OBSERVATIONS` 或 `NON_MOCK_OBSERVATIONS_REQUIRED`。

## 2. 求解器支持矩阵与降级规则

| 能力 | `swmm_kw` | `swmm_dw` | `py_mw` | `py_dw` | `py_ponding` |
|---|---:|---:|---:|---:|---:|
| 重力管、节点连续性、树/环 | 支持 | 支持 | 支持 | 支持，限缓坡亚临界 | 不适用 |
| 圆形/箱形/矩形管 | 支持 | 支持 | 支持 | v1 限圆形/矩形 | 不适用 |
| 满管/压力流 | 运动波限制 | 支持 | 支持容量受限近似 | 不支持 | 不适用 |
| 泵、闸、堰、孔口 | 支持 | 支持 | v1 不支持 | 不支持 | 不适用 |
| 调蓄池/节点库容 | 支持 | 支持 | 支持 | 支持 | 不适用 |
| RDII、LID、积雪、treatment | 支持 | 支持 | 不支持 | 不支持 | 不支持 |
| SWMM 二维地表水 | 依配置 | 依配置 | 不支持 | 不支持 | 不支持 |
| 显式积水单元 | 不作为独立模型 | 不作为独立模型 | 可组合 | 可组合 | 支持 |
| 5000/10000/24h ≤180s | 需基准 | 需基准 | 首发硬门禁 | 不承诺 | 不承诺 |

**执行规则：**

1. 提交前由目标 solver 的 `capabilities()` 与输入交叉校验。
2. 目标 solver 不支持的对象/选项必须在仿真开始前返回 `UNSUPPORTED_FEATURE`，错误中列出 `object_ids`、`features` 和建议 solver。
3. INP 导入可以做“全量语法/拓扑校验”，但运行前必须做“目标 solver 能力校验”；导入成功不代表可运行。
4. 用户主动请求 `allow_degraded=true` 也不能删除压力流、泵、节点或控制对象；只能接受已知有界近似，并在每个受影响对象的输出中携带 `degraded_features`。核心拓扑缺失不得降级。
5. `py_dw` 出现超临界流、快速流向反转或压力流转段时必须失败为 `NUMERICAL_UNSTABLE`/`UNSUPPORTED_REGIME`，不得继续给出“稳定”结果。

## 3. 规范数据合同

### 3.1 标识、版本与可复现性

每个仿真必须保存以下不可变指纹：

- `network_revision_id` + `target_manifest_id/content_sha256`；
- `network_content_hash` 与 `snapshot_schema/properties_schema/transform` hash：对强类型 canonical snapshot 做 SHA-256；
- `rainfall_scenario_id` 与 `rainfall_content_hash`；
- `solver_id`、`solver_version`、`engine_binary_sha256`（SWMM 必填）；
- `solver_config_hash`：时间步、报告间隔、初值、参数和数值容差的规范哈希；
- `code_revision`；
- `created_at`、所有时间戳均为 UTC RFC 3339。

同一 `idempotency_key` 在同一用户、网络快照、降雨、solver 和配置下返回同一任务；任何影响结果的字段改变都必须产生新的 request fingerprint。

### 3.2 单位和方向

所有持久化/API 字段必须带单位，禁止无单位 `level`、`slope`、`flow`：

| 量 | 规范单位 | 约束 |
|---|---|---|
| 长度、宽度、高程、积水深 | m | API 使用 `*_m` |
| 管径/高度/宽度 | m | 内部求解使用 m |
| 管长 | m | 禁止直接把经纬度差当长度 |
| 流量 | m³/s | API 使用 `flow_m3s` |
| 体积 | m³ | 守恒台账使用 m³ |
| 降雨强度 | mm/h | 导入后换算；`1 mm/h = 2.7777777777777776e-7 m/s` |
| 时间 | UTC RFC 3339 / duration seconds | 不接受无时区时间 |
| 糙率 | Manning n | 正数，必须有来源/默认值标记 |
| 坡度 | dimensionless rise/run | 正负号保留，不得取绝对值掩盖逆坡 |

- 几何存储保留输入 CRS；长度、坡度和面积必须由明确的投影 CRS 或测量长度计算。
- 管段方向由 `from_node_id -> to_node_id` 唯一确定；高程差方向不可反转。
- node `crown_elev_m <= rim_elev_m`；缺少管底/管顶关系时不得以水面高程代替管底。

### 3.3 首轮 `NetworkSnapshot` 最小字段（历史；已被 v2 强类型契约取代）

> 实施必须读取 `IMPLEMENTATION_CONTRACT.md:3.3.1-3.3.3`：`NetworkSnapshot` 闭合字段、强类型 `PondCell`/`BoundaryConditionSnapshot`/`InitialStateSnapshot`、closed properties schema 和 revision-scoped target manifest 是唯一规范。下列字段仅说明首轮意图，缺一不能视为 v2 通过。

#### Node

```text
node_id: UUID
kind: junction | outfall | storage | inlet | pond_boundary
x, y: float
geometry: WKT/GeoJSON 或 PostGIS geometry
invert_elev_m: float
crown_elev_m: float | null
rim_elev_m: float
initial_depth_m: float >= 0
boundary_type: none | free_outfall | fixed_stage | rating_curve
boundary_parameters: versioned object
```

#### PipeSegment

```text
pipe_id: UUID
from_node_id: UUID
to_node_id: UUID
length_m: float > 0
roughness_n: float > 0
shape: circular | rectangular | box
diameter_m | height_m + width_m: float > 0
invert_from_elev_m: float
invert_to_elev_m: float
profile_id: UUID | null
profile_point_count: integer
initial_flow_m3s: float = 0
```

`crown` 可由截面和 invert 计算，但必须写入规范化快照；API 不应要求客户端重复提供可推导值。

#### Catchment

```text
catchment_id: UUID
outlet_node_id: UUID
area_m2: float > 0
width_m: float > 0
slope_percent: finite float
impervious_fraction: 0..1
depression_storage_mm: >= 0
infiltration_model: horton | green_ampt | curve_number
infiltration_parameters: versioned object
```

#### PondCell（只有启用 `py_ponding` 时必填）

```text
pond_cell_id: UUID
area_m2: float > 0
elevation_m: float
roughness_n: float > 0
depression_storage_m: float >= 0
geometry: WKT/GeoJSON 或 PostGIS geometry
links: tuple[PondCellLink{counterpart_stable_key,direction,crest_m,width_m,roughness_n,max_capacity_m3s}, ...]
sewer_exchange_links: tuple[PondSewerExchange{node_stable_key,direction,capacity_schema_version,typed_capacity_parameters,enabled}, ...]
stage_area_curve: non-empty strictly-increasing tuple[PondStageArea{stage_m,area_m2}, ...]
```

### 3.4 `RainfallSource`

抽象接口固定为：

```python
class RainfallSource(Protocol):
    source_id: str
    version: str

    def materialize(self, request: RainfallRequest) -> RainfallSeries: ...
```

`RainfallSeries` 必须包含：

- `start_at`, `end_at`；
- 原生步长和目标内部步长；
- `intensity_mm_h[]`；
- 面积权重或 catchment 分配方式；
- `seed`、随机数算法和算法版本；
- 总量 `total_depth_mm`；
- 内容 hash。

随机雨型必须用固定算法、固定 seed 和固定采样顺序；同一请求重跑得到逐字节相同输入。`source="mock"` 只能进入演示和测试。

### 3.5 `SolveRequest`

```text
simulation_id: UUID
network_revision_id: UUID
target_manifest_id: UUID
initial_state_id: UUID | null
rainfall_scenario_id: UUID
solver_id: swmm_kw | swmm_dw | py_mw | py_dw
routing_mode: kw | dw | diffusion_wave（只由 solver_id 推导，调用方不得矛盾组合）
ponding_enabled: bool
simulation_start_at: UTC
duration_s: > 0, <= 72h for custom rainfall scenario
rain_step_s: allowed source step
reporting_interval_s: > 0 and <= 300
initial_state_ref: dry | warm_state | uploaded_initial_state
parameter_set_id: UUID
idempotency_key: string
```

`solver_id` 与 `routing_mode` 不一致时返回 422，不自动纠正。

### 3.6 `SimulationFrame` 与诊断

每个 frame 包含 `attempt_id`、`simulation_id`、`frame_index`、`valid_at`、`network_revision_id`、`target_manifest_id/hash`、`solver_id/version` 和 `quality_status`。

- node state：`depth_m`, `hydraulic_head_m`, `water_surface_elev_m`, `volume_m3`, `surcharged`, `state`；
- pipe state：`flow_m3s`, `velocity_mps`, `depth_m`, `fill_ratio`, `state`；
- ponding state（若有）：`cell_id`, `water_surface_elev_m`, `depth_m`, `volume_m3`, `overflow_m3s`；
- diagnostics：`mass_residual_m3`, `mass_residual_relative`, `min_time_step_s`, `max_courant`, `substep_count`, `warnings[]`, `degraded_features[]`。

状态字段只使用枚举：`dry`, `surface_flow`, `pressurized`, `surcharged`, `disconnected`, `degraded`。禁止用负深度/负体积表示空气状态。

## 4. 求解器接口合同

```python
from typing import AsyncIterator, Protocol

class HydraulicSolver(Protocol):
    solver_id: str
    version: str

    def capabilities(self) -> "SolverCapabilities": ...
    def validate(
        self, snapshot: "NetworkSnapshot", request: "SolveRequest"
    ) -> "ValidationReport": ...

    async def run(
        self,
        request: "SolveRequest",
        snapshot: "NetworkSnapshot",
        frame_sink: "FrameSink",
        control: "RunControl",
    ) -> "SolveSummary": ...
```

### 4.1 `FrameSink`

```python
class FrameSink(Protocol):
    async def write(self, frame: "SimulationFrame") -> None: ...
    async def finalize(self, summary: "SolveSummary") -> None: ...
```

- sink 必须按 reporting interval 流式写入当前 attempt；solver 不得先构造全量 frame list。
- `write` 以 `(attempt_id, frame_index, payload_hash, fencing_token)` 幂等；不同 hash/旧 token 明确失败。
- retry 创建新的 attempt staging，旧 attempt 不可见且不可复用；`finalize` 原子发布前任务不得进入 `completed`。
- 数据库批次写入失败由 sink 抛出，solver 不得吞掉。

### 4.2 `RunControl`

至少包含：

- `deadline_at`；
- `cancel_event`；
- `progress_cb(progress_pct, phase)`；
- `workspace_root`；
- `max_workspace_bytes`；
- `max_substeps_per_report_interval`；
- `numerical_retry_profile`。

取消检查必须不晚于每 1 秒一次；外部进程还必须由独立 process-tree supervisor 终止。

### 4.3 `SolveSummary`

必须包含：

- `solver_id`, `solver_version`, `engine_binary_sha256?`；
- `model_fidelity`；
- `network_content_hash`, `rainfall_content_hash`, `solver_config_hash`；
- `frame_count`, `first_at`, `last_at`；
- 总/最大相对质量残差；
- `min_time_step_s`, `max_courant`；
- `total_input_m3`, `final_storage_m3`, `outfall_m3`, `infiltration_m3`, `overflow_m3`；
- `unsupported_or_degraded_features[]`；
- `warnings[]`, `diagnostics_blob_ref`；
- `result_status = verified | warning | failed`。

## 5. 数值算法边界

### 5.1 EPA SWMM adapter

- 独立进程运行固定路径、固定 SHA-256 的 EPA SWMM engine；API/worker 进程不得加载未校验 DLL。
- 仅允许参数数组启动，`shell=False`；不得拼接用户字符串。
- 输入、报告和二进制输出都位于不可预测的 task UUID 目录；用户文件名不作为最终路径。
- 运行前执行版本/sha 自检；版本不匹配返回 `SOLVER_VERSION_MISMATCH`。
- `.rpt` 中 warning/error、continuity error、计算时间和 routing step 必须结构化记录。
- `.out` 必须由与 engine 版本绑定的解析器读取；禁止用无版本 `struct.unpack` 猜布局。
- 若无法产生可解析的完整结果，不得用 `.rpt` 汇总值伪造节点/管段帧。
- SWMM adapter 不自动切换 `py_*`。如用户要比较 fallback，必须另建 simulation。

### 5.2 `py_mw`

- 管网状态为每管段流量/水深和每节点蓄量；节点方程强制全网连续性。
- 管段摩阻使用形状相关的 Manning 水力关系；环路由同一节点连续性方程闭合，不能先树化后忽略环路。
- 满管/压力流转为容量受限的 Manning 满管关系，并显式记录 `pressurized`/`surcharged`；不解析水锤。
- 内部时间步自适应，但不得超过第 8 节稳定条件。
- 结果标记 `fast_screening`；即使守恒通过，也不自动标记为工程设计级。

### 5.3 `py_dw`

- 采用守恒的管段有限体积/有限差分通量形式，动量方程显式省略局部惯性项；流量/水深更新必须满足单元正性和节点连续性。
- 只接受缓坡、亚临界重力流。Froude number 达到 0.95、流向在一个时间窗内快速反转、或需要压力流时立即停止并报告 `UNSUPPORTED_REGIME`。
- 不支持泵、闸、堰、孔口、水锤、瞬变边界。
- 必须有解析解/小网格收敛测试：单管均匀流、静水、线性水面、缓坡回水、闭合环。
- `py_dw` 与 `swmm_dw` 只做诊断比较，不要求逐点相等；报告 RMSE、NSE、峰值误差和质量残差，并保留两者 `solver_id`。

### 5.4 `py_ponding`

- 每个 cell 使用水量守恒的蓄量方程；cell 间使用有方向、有限宽度的 Manning/堰流关系。
- 排水口/进水口交换是有记录的内部 transfer：同一 m³ 从地表进入管网后，不得再次计为外部降雨输入。
- cell 状态由 `py_ponding` 单独求解；不得将一维节点水位直接伪装成二维地表水动力。
- 底图淹没多边形必须从 cell 几何合成，并带 `simplified_extent=true`。

## 6. 守恒合同

### 6.1 单一质量台账

每次仿真必须维护：

```text
epsilon_m3 = initial_storage_m3
          + rainfall_input_m3
          + external_boundary_inflow_m3
          - infiltration_loss_m3
          - outfall_discharge_m3
          - external_boundary_outflow_m3
          - final_storage_m3
```

相对残差：

```text
relative_error = abs(epsilon_m3) /
                 max(1.0, rainfall_input_m3 + external_boundary_inflow_m3
                                + initial_storage_m3)
```

### 6.2 门禁

- `py_*`：任一 frame 和全程 `relative_error <= 1e-4` 才能发布 `verified`；`1e-4 < error <= 1e-3` 为 `warning` 且不得产生高等级自动告警；`>1e-3` 失败为 `MASS_BALANCE_FAILED`。
- `swmm_*`：产品自己的外部输入/最终库容台账同样必须 `<=1e-3`；同时必须保存 SWMM runoff/routing continuity error。缺 continuity error 时质量状态最多为 `warning_unverified`，不能通过 AC-3 的质量门禁。
- 所有状态必须为有限数；`depth_m >= 0`、`volume_m3 >= 0`。
- 小于 `1e-9 m` 或 `1e-9 m³` 的负值可归零，但归零量必须累计到 `numerical_correction_m3` 并进入残差。
- 大于上述容差的负体积/水深视为数值失败，禁止用 `max(0, value)` 静默修复。
- `continuity_error` 不能替代产品台账；反过来也一样。

### 6.3 防双计

以下均使用 transfer ledger，不重复计入外部输入：

- 汇水区径流进入节点；
- 节点溢流进入 `PondCell`；
- `PondCell` 流入节点；
- 节点到调蓄池；
- 网络到 outfall 再进入地表边界。

每个 transfer 有 `from_kind/from_id/to_kind/to_id/amount_m3/frame_index`；frame 摘要可聚合，但 debug 模式必须可追溯。

### 6.4 必测守恒 fixture

1. 单一封闭管段施加已知体积脉冲，末端无流，最终体积增量精确匹配。
2. 水平闭合环在相同水头下无虚假流量。
3. 零降雨、已知初始库容的蓄水池自由排空，积分出流等于初始库容减损失。
4. catchment → node → outfall 单路径，台账每项只出现一次。
5. node ↔ ponding 双向交换往复 100 次，无逐次质量增益。
6. `py_mw`、`py_dw` 和 SWMM adapter 使用同一小 fixture 各自给出台账；数值结果可不同，质量台账各自必须闭合。

## 7. 初始、边界与参数

1. 默认初始状态为 dry 只能在所有节点 `initial_depth_m=0` 且 `py_ponding` cell `initial_depth=0` 时使用。
2. warm state 必须引用不可变 `initial_state_id`，包含每个对象初值、来源观测时间和 hash；不能隐式使用“上次同网络结果”。
3. free outfall、fixed stage、rating curve 必须在网络快照中版本化；缺参数在运行前失败。
4. 默认参数与率定参数必须分别有 `parameter_set_id`；结果和 A/B 报告不得只写“default/calibrated”文本。
5. 水力参数改变时创建新 simulation；不得原地修改已提交任务。
6. 观测只可用于初始 warm-up、状态同化或率定，角色必须在报告中分开；不能把未来观测复制到评估输入。

## 8. 稳定性合同

### 8.1 自适应时间步

初始步长：

```text
dt0 = min(rain_step_s, reporting_interval_s, 60)
```

每个非恒定步必须满足：

```text
Courant = dt * (abs(u) + sqrt(g * max(h, h_floor))) / min_control_length <= 0.5
```

其中 `h_floor = 1e-4 m`，`min_control_length` 是当前管段/积水 link 的最小受控长度。发生以下情况时缩小步长：

- 降雨强度边界变化；
- 干湿/自由水面/压力流转换；
- 流向反转；
- 节点水深差快速变化；
- 残差或迭代不收敛。

每个 reporting interval 最多 `max_substeps_per_report_interval=20` 个内部步；仍无法满足稳定条件时失败为 `NUMERICAL_STABILITY_LIMIT`，不得用超大时间步继续。

### 8.2 数值诊断

每个 frame 至少记录：

- `max_courant`；
- `min_time_step_s`；
- `substep_count`；
- 迭代残差/未收敛次数；
- `numerical_correction_m3`；
- `negative_state_events`；
- `regime_transition_count`。

以下任一情况不得返回 `verified`：

- NaN/Inf；
- Courant 超过 1.0；
- 连续 3 个 reporting interval 未收敛；
- 负体积超过数值容差；
- 超临界/压力流进入 `py_dw` 未支持区间；
- 用户指定 `strict_stability=true` 时 Courant 超过 0.5；
- 质量残差超过第 6 节门禁。

### 8.3 稳定性测试矩阵

每个 Python solver 至少覆盖：

- dry → shallow flow → dry；
- 干湿锋；
- 平坡静水；
- 缓坡回水；
- 环网同时涨落；
- 满管/压力流（`py_mw`）；
- 超临界/流向反转拒绝（`py_dw`）；
- 短管集中入流；
- 降雨脉冲；
- 确定性重复运行 bitwise/metric-identical；
- 1/2/4 worker 并发不改变单任务结果。

## 9. 任务、队列、超时、重试与取消

### 9.1 事实源

PostgreSQL/TimescaleDB 是任务状态和结果事实源；Redis 只能作为 worker wakeup/fanout。不得以 Redis Pub/Sub 或内存 SSE history 表示任务是否完成。

最低持久字段：

```text
simulation_id, request_fingerprint, idempotency_key,
status, progress_pct, phase, attempt, max_attempts,
lease_owner, lease_expires_at, cancel_requested_at,
deadline_at, solver_id, solver_version, network_revision_id, target_manifest_id, initial_state_id,
rainfall_scenario_id, error_code, error_detail, created_by,
started_at, finished_at
```

### 9.2 状态机

```text
queued -> leased -> running -> completed
queued -> cancelled
leased/running -> retry_wait -> leased
queued/leased/running -> cancelling -> cancelled
leased/running -> failed | timed_out
```

- `completed` 仅在 attempt 全部 frame + summary + quality 原子发布后进入；重新提交相同请求仍返回原终态任务。
- 重复 transition 使用行版本/条件更新；并发取消与完成时，`succeeded` 只有在结果已持久化后才可赢，否则 `cancelled` 可赢。
- `retry_wait` 必须记录 retryable error、退避时间和下一次 attempt。

### 9.3 并发、租约和恢复

- `SIM_GLOBAL_CONCURRENCY=2` 指整个部署的总求解槽位，不只是单进程 semaphore。
- claim 使用数据库租约/可恢复队列语义；不得使用无 ack 的内存 `asyncio.Queue`。
- worker 每 10 秒续租；租约默认 30 秒。过期 `running` 任务由恢复扫描重新排队或终止，取决于 attempt 和 error。
- frame 唯一键 `(simulation_id, frame_index)`；重放/恢复写入必须幂等。
- 队列深度、最老任务年龄和租约过期数必须有 Prometheus 指标。

### 9.4 超时与重试

- 每次 attempt 的硬超时为 600s；排队时间不计入 attempt，但 API 不得承诺无限排队。
- `max_attempts=3`，即首次执行 + 最多 2 次重试。
- 数值不稳定允许按冻结的 refinement profile 缩小 `dt` 后重试；每次重试必须记录新 config hash。
- 外部进程异常退出、临时 I/O 错误、数据库提交失败可重试。
- 输入非法、能力不支持、用户取消、20 MiB 输入超限、结果写超配额不得自动重试。
- timeout 默认不自动重试；如产品配置允许，必须显式设置且 API 展示总预算，避免单个请求占用 30 分钟。
- 每次重试前清理旧 task 目录；禁止把上次的 `.out` 当本次结果。

### 9.5 取消

- queued 取消立即进入 `cancelled`。
- running 取消先写 `cancel_requested_at`，worker 在最多 1 秒内检查并停止。
- SWMM/其他外部进程必须终止整个进程树；仅关闭父进程不算成功。
- cancel 是幂等的；已 terminal 返回 409 和当前终态。
- 取消/超时/崩溃路径都必须在 finally 清理 task 目录，并写结构化日志，含 `simulation_id`, `attempt`, `solver_id`, `phase`。

### 9.6 进度

进度为持久、单调的 0..100 整数：

- 0：已创建；
- 1..4：输入解析/校验；
- 5..79：求解；
- 80..94：结果解析/质量检查；
- 95..99：分帧持久化和摘要；
- 100：全部 frame + summary + quality report 已在同一成功提交中持久化。

SWMM CLI 通常不能给出真实物理百分比，因此只能报告上述阶段估算；不得伪造逐时间步精度。重试时 API 显示的 `progress_pct` 不回退，但 response 中另有 `attempt` 和 `phase`。

## 10. 结果持久化与 90 天保留

1. 节点、管段、积水帧写入 Timescale hypertable；`valid_at` 为时间列。
2. 任务 summary、质量台账、参数和 calibration link 存普通关系表；不能只存在临时文件或 Redis。
3. 默认 retention 为 90 天，可由 `RESULT_RETENTION_DAYS` 配置；删除以 simulation 为单位或按 Timescale policy，删除后保留最小审计 tombstone。
4. API 查询按 `valid_at`、对象分页；不得默认一次返回 4,335,000 行。
5. 导出 GeoJSON 不得把 289 帧全部嵌入单个 feature；提供单帧/时间范围导出，或 NDJSON/Parquet 清单。
6. 90 天到期不删除仍被 calibration/audit 引用的模型版本、参数和报告元数据；只清理大体量 frames。

## 11. 率定与性能比较合同

### 11.1 观测准入

观测必须先有唯一 ingest event：

```text
source_system, source_stream, source_partition, source_offset,
source_event_id, source_payload_sha256, adapter_version, received_at
```

随后 sample 记录包含：

```text
ingest_event_id, observed_at, target_type, target_stable_key,
metric, value, unit, quality_flag, source_point_ref
```

准入规则：

- `source="mock"`/legacy unverifiable 不可用于 AC-5；同 source event identity + 同 payload 重放幂等，不同 payload 为 `SOURCE_EVENT_CONFLICT`；
- 目标必须通过 `network_revision_id + stable_key + mapping_version` 绑定；无 revision 归属的点拒绝；
- variable/unit 必须映射到 `depth_m` 或 `flow_m3s`；
- 必须绑定 `network_revision_id + target_stable_key + mapping_version`；
- 质量标志为 bad/missing 的点不参与指标；
- 不允许在评估时刻用未来观测插值。

### 11.2 时间切分和样本门槛

- 至少 3 个独立降雨/运行事件；
- 整个事件只能属于 calibration/validation/test 之一，禁止把同一事件切到两边；
- 按事件持续时间做 60%/20%/20% 或 70%/15%/15% 切分，报告实际事件/时间占比；
- 每个评估集合至少 12 个有效 paired observations；否则指标只作诊断，不能判 AC-5 通过。

### 11.3 指标

对流量/水位使用同一时间点、同一单位配对：

```text
RMSE = sqrt(mean((obs - sim)^2))
NSE = 1 - sum((obs - sim)^2) / sum((obs - mean(obs))^2)
```

- NSE 分母为 0 时返回 `NSE_UNDEFINED`，不得伪造 0 或 1。
- 同时报告 Bias、峰值误差、样本数、缺测率、覆盖时段和配对容差。
- 水位绝对误差使用 m；不得在图表上把 m 与 mm 混用。

### 11.4 基线和通过条件

基线与率定版必须使用相同网络快照、观测切分、降雨、边界、初值、solver/version 和评估代码，只允许声明的 hydraulic parameter set 不同。

本产品的**演示验收门槛**定义为：

- validation 或 test（预先指定，不得事后挑选）上 `NSE_calibrated - NSE_baseline >= 0.05`；
- 同一集合 `RMSE_calibrated <= 0.95 * RMSE_baseline`；
- peak absolute error 不得恶化超过 10%；
- 没有守恒或稳定性失败。

该门槛是产品 AC-5 的可重复判据，不等同于科学研究或工程认证。未达到时可以返回报告，但 `improved=false`，不得宣称“率定成功”。

### 11.5 可率定参数边界

v1 只允许白名单参数：Manning roughness multiplier、depression storage、infiltration 参数、catchment width/slope correction、initial loss/remaining parameters。必须持久化：

- 参数名、before/after、物理上下界；
- 优化算法/随机 seed、迭代/停止条件；
- objective、目标函数权重；
- 校准集与验证集分离证据；
- 选定 parameter set 的 hash。

## 12. 性能合同与验收数据集

### 12.1 固定 benchmark fixture

必须提交版本化 fixture，不允许用全干网络制造假性能：

- 5000 nodes、10000 pipes、1000 catchments；
- 至少 10% pipes 构成独立环，其余为树；
- 包含 5% storage/outfall、合法逆坡告警但求解有效段、多个初始水深；
- 24h 降雨，至少 288 个 1 分钟源步，报告间隔 300s；
- 峰值径流使目标管段非零且发生回水/环流；
- 1000 pond cells 的场景单独 benchmark，不与“纯管网 180s”混写；
- fixture manifest 包含对象计数、拓扑 hash、几何边界、降雨 hash 和期望非零对象比例。

### 12.2 环境与计时

- Python 3.11 锁定环境；
- 4 个物理核，禁用 GPU，限制线程池/OMP/BLAS 总线程为 4；
- 单任务独占 4 核测量；报告 wall time、CPU time、峰值 RSS；
- `t0` 为 worker claim 成功后；`t1` 为 summary/quality/所有 frame 持久化后；
- 不计 queue wait，但单独报告 queue wait；
- 预热一次，正式连续 3 次，每次都须 `t1-t0 <= 180s`；
- 每次结果 content hash 与质量指标必须在容差内一致。

### 12.3 分项报告

```text
input_validation_s
solver_s
parse_s
frame_serialize_s
database_persist_s
queue_wait_s
total_worker_s
frame_rows
raw_or_columnar_output_bytes
peak_rss_bytes
mass_residual_max
min_time_step_s
max_courant
```

如果总时长通过但质量门禁失败，性能 AC 仍失败。API P95 <500ms 另行测量查询路径，不把 180s 求解时间算入普通 CRUD/结果元数据 API。

### 12.4 降级规模门禁

`py_dw` 和 `py_ponding` 在获得数据前不得发布“5000/10000 可处理”的宣传。首个可发布规模必须由同一个 benchmark 生成，例如 500/1000、1000/2000；未通过前 API 能力描述只写 `validated_scale=null`。

## 13. 失败语义与 API 映射

所有新错误保持顶层 `detail`，并使用 `errors[]` 承载结构化信息：

```json
{
  "detail": "simulation input contains unsupported features",
  "errors": [
    {
      "code": "UNSUPPORTED_FEATURE",
      "simulation_id": "...",
      "field": "pumps",
      "retryable": false,
      "details": {"object_ids": ["..."], "suggested_solver": "swmm_kw"}
    }
  ]
}
```

| 错误码 | 含义 | HTTP/任务语义 | 自动重试 |
|---|---|---|---:|
| `NETWORK_INVALID` | 拓扑/几何/高程非法 | submit 422；不创建可运行任务 | 否 |
| `UNSUPPORTED_FEATURE` | 目标 solver 不支持 | submit 422，列出对象/选项 | 否 |
| `INPUT_TOO_LARGE` | 超过 20 MiB 或结构限额 | 413/422；服务保持可用 | 否 |
| `UNSAFE_PATH` | 路径穿越/非白名单文件 | 400；隔离并清理 | 否 |
| `SOLVER_UNAVAILABLE` | 指定引擎不存在/健康失败 | submit 503 或 job failed | 仅基础设施瞬态 |
| `SOLVER_VERSION_MISMATCH` | 二进制 sha/版本不符 | 503；禁止运行 | 否 |
| `NUMERICAL_UNSTABLE` | 不收敛/非法流态 | job failed | 是，缩小 dt，最多 2 次 |
| `NUMERICAL_STABILITY_LIMIT` | 20 substeps 仍不满足 CFL | job failed | 是，最多 2 次 |
| `MASS_BALANCE_FAILED` | 质量台账超门限 | 不发布结果，不发 forecast | 否 |
| `OUTPUT_LIMIT_EXCEEDED` | 工作区/结果写超配额 | job failed | 否 |
| `SIMULATION_TIMEOUT` | 单 attempt >600s | terminal `timed_out` | 默认否 |
| `SIMULATION_CANCELLED` | 用户取消 | terminal `cancelled` | 否 |
| `RESULT_PERSIST_FAILED` | 帧/摘要提交失败 | job failed或可恢复重试 | 是 |
| `INSUFFICIENT_OBSERVATIONS` | 率定样本不足 | 422；报告仍可查询 | 否 |
| `NON_MOCK_OBSERVATIONS_REQUIRED` | AC-5 只用了 mock | 422 | 否 |

任务 API 还必须：

- 重复 submit 返回同一 `simulation_id`；
- 取消未知 ID 为 404，终态 ID 为 409，已取消重复取消可返回 200/202 的幂等结果；
- 所有终态响应包含 `error_code`, `attempt`, `finished_at`；
- 不允许 HTTP 500 代替已知的 409/422/503 业务失败。

## 14. 文件所有权

为避免继续扩张现有单文件路由和 worker，实现必须按下列所有权拆分；一个文件只能有一个业务所有者。

| 文件/目录 | 唯一所有权 | 禁止事项 |
|---|---|---|
| `backend/app/hydraulics/domain/contracts.py` | enum、单位、状态、错误码、协议 DTO | 不访问 DB/文件/solver |
| `backend/app/hydraulics/domain/units.py` | 单位和换算 | 不含模型逻辑 |
| `backend/app/hydraulics/solvers/base.py` | `HydraulicSolver`、`FrameSink`、`RunControl` 接口 | 不实现具体数值法 |
| `backend/app/hydraulics/solvers/registry.py` | solver 能力注册与选择 | 不静默 fallback |
| `backend/app/hydraulics/solvers/swmm_adapter.py` | 外部 EPA SWMM 进程、版本、rpt/out parser | 不包含 API/JWT/DB ORM |
| `backend/app/hydraulics/solvers/python_mw.py` | 纯 Python 运动波数值法 | 不访问网络/DB/用户路径 |
| `backend/app/hydraulics/solvers/python_dw.py` | 纯 Python 扩散波数值法 | 不复用 MW 状态冒充 DW |
| `backend/app/hydraulics/solvers/simple_ponding.py` | `PondCell` 与简化积水 | 不宣称 SWMM 2D |
| `backend/app/hydraulics/quality/mass_balance.py` | 质量台账和 transfer ledger | 不告警/不发 SSE |
| `backend/app/hydraulics/quality/stability.py` | CFL、substep、状态诊断 | 不决定 HTTP 状态码 |
| `backend/app/hydraulics/results/frame_sink.py` | 流式分帧、幂等持久化 | 不执行业务告警 |
| `backend/app/hydraulics/jobs/repository.py` | simulation/job/lease 数据访问 | 不含数值算法 |
| `backend/app/hydraulics/jobs/service.py` | 状态机、幂等、取消、retry policy | 不直接 import SWMM binary |
| `backend/app/hydraulics/worker.py` | claim/lease/调用 solver/sink/recovery | 不定义 API schema |
| `backend/app/hydraulics/calibration/service.py` | 观测对齐、指标、参数报告 | 不修改原始观测 |
| `backend/app/api/routes_hydraulics.py` | networks/rainfall/simulations/forecast 路由和 DTO | 不含数值法或 SQLAlchemy 查询实现 |
| `backend/alembic/versions/<hydraulic_revision>.py` | hydraulic schema/索引/hypertable/retention | 不手改 `docker/init-db.sql` |
| `docker/solver/` | 固定 engine 工件、checksums、Windows/Linux 启动包装 | 不放用户数据 |
| `backend/tests/hydraulics/` | 单元、golden、contract、稳定性、质量测试 | 不以 mock observation 通过 AC-5 |
| `benchmarks/hydraulics/` | 固定性能 fixture、runner、机器可读报告 | 不进入 API 进程 import path |

现有 `backend/app/api/routes.py` 只负责组合 router；现有 `main.py` 不拥有仿真业务逻辑。SWMM adapter、纯 Python solver 和 worker 必须能在没有 FastAPI app 的情况下单测。

## 15. 验收证据合同

### 15.1 必交测试文件与语义

| 测试 | 必须证明 |
|---|---|
| `test_solver_contracts.py` | 所有 solver 遵守同一接口、capability、summary 和 frame schema |
| `test_swmm_adapter.py` | 固定 engine/version/hash；argv 无 shell；rpt/out 解析；缺 engine/坏版本明确失败 |
| `test_swmm_roundtrip.py` | 同一规范网络导出 INP 再导入，网络/降雨/关键参数 hash 等价或列出显式有损映射 |
| `test_python_mw.py` | 单管、树、环、满管、压力流、确定性 |
| `test_python_dw.py` | 静水/均匀流/回水/收敛；超临界和压力流拒绝 |
| `test_simple_ponding.py` | cell 库容、cell link、sewer exchange、简化淹没范围标识 |
| `test_mass_balance.py` | 第 6.4 节全部 fixture；残差门禁 |
| `test_stability.py` | CFL、自适应 dt、20-substep 上限、NaN/负状态失败 |
| `test_task_lifecycle.py` | idempotency、进度单调、取消、超时、2 次重试、lease 恢复、终态不可回退 |
| `test_sandbox.py` | 20 MiB 解压上限、POSIX/Windows 路径、symlink/junction、进程树和 finally 清理 |
| `test_calibration.py` | 时间泄漏、mock 拒绝、指标公式、NSE=0 分母、阈值、参数 hash |
| `test_ab_comparison.py` | 仅允许参数不同的 fingerprint 对比和可复现报告 |
| `test_perf_contract.py` | 小规模 CI guard；完整 5k/10k benchmark 独立运行 |
| `test_api_contract.py` | OpenAPI、错误 additive envelope、RBAC、existing route zero regression |

### 15.2 建议验收命令（待实现后运行；本次均未运行）

```bash
cd backend
python -m pytest tests/hydraulics/test_solver_contracts.py -q
python -m pytest tests/hydraulics/test_mass_balance.py tests/hydraulics/test_stability.py -q
python -m pytest tests/hydraulics/test_task_lifecycle.py tests/hydraulics/test_sandbox.py -q
python -m pytest tests/hydraulics/test_calibration.py tests/hydraulics/test_ab_comparison.py -q
python -m pytest tests/hydraulics -q
```

完整性能必须在受限 4 核容器执行，命令形态固定为：

```bash
docker compose run --rm --cpus=4 --memory=8g simulation-benchmark \
  python -m benchmarks.hydraulics.run --fixture 5k_10k_24h_v1 --repeats 3
```

`8g` 是建议的 benchmark 资源上限，不是新增产品约束；最终 runner 必须把实际限制写入报告。Windows 另需在 Python 3.11 原生环境执行同一 fixture，并输出 `platform`, `processor`, `python`, `numpy/scipy`, `engine` 版本。

### 15.3 每项必须留存的人工/机器证据

- 规范网络和 rainfall 的 canonical JSON + SHA-256；
- 生成的 INP、原始 `.rpt`、解析后的 quality report、engine binary SHA-256；
- Python solver 的 frame golden（允许用 Parquet/NPZ，不要求巨型 JSON）；
- 质量台账、CFL/时间步报告；
- 任务状态/重试/恢复日志，所有条目含 `simulation_id`；
- 90 天 retention 的 Timescale policy 查询；
- calibration 的事件切分、观测来源、参数和指标报告；
- A/B fingerprint 差异报告；
- 5k/10k 三次性能原始结果、环境、峰值 RSS、frame count 和 output bytes；
- full pytest/vitest/build/Compose 结果。现有报告已经显示不同环境下旧测试结果不一致，因此必须以目标 Python 3.11/Node 20 的同一提交结果为准（`docs/hydraulic-twin/recon/quality.md:45-71,96-137,380-391`）。

## 16. 本次检查状态

| 检查 | 状态 | 原因/证据 |
|---|---|---|
| 阅读六份 recon | **已执行** | 已读 backend-data、realtime-security、frontend、simulation、deployment、quality |
| 读取 CPU requirements | **已执行** | `requirements-cpu.txt:0-33` 无水力求解依赖 |
| 读取 Compose | **已执行** | `docker-compose.yml:7-177` 无 simulation worker |
| 读取 Redis/SSE/错误合同 | **已执行** | `redis_client.py:82-164`、`sse_manager.py:32-189`、`main.py:313-335` |
| SWMM/纯 Python 数值测试 | **未运行** | 当前无实现/engine/fixture；本任务只读源码并写契约 |
| 5k/10k/24h 4 核 benchmark | **未运行** | 当前无 solver 与 benchmark runner |
| 守恒/稳定性测试 | **未运行** | 当前无 solver |
| RMSE/NSE/AC-5 | **未运行且受真实观测缺口阻断** | 无真实节点映射和非 mock 观测 |
| Compose worker/Windows/90 天 retention | **未运行** | 当前无 worker/schema；既有报告也未运行 Docker daemon 相关检查 |

## 17. 发布阻断条件

满足以下任一项时，不得宣称水力数字孪生/预测告警完成：

1. 没有固定版本 SWMM adapter 或明确 `solver_id/version/hash`；
2. `py_mw`/`py_dw` 没有守恒、稳定性和确定性 golden；
3. `py_ponding` 被展示成 SWMM 2D；
4. SWMM 失败会静默降级或在同一 `simulation_id` 下换 solver；
5. 任务没有持久 lease、幂等、取消、600s timeout、最多 2 retry 和恢复证据；
6. 5 分钟全量帧在 5000/10000 fixture 上未验证持久化和 90 天 retention；
7. 180s benchmark 未在固定 4 核环境实际执行；
8. AC-5 只有 mock 观测，或没有 baseline/calibrated 同条件对比；
9. 静默/高等级告警没有 `model_fidelity`、质量状态和 `simulation_id`；
10. 用户输入超过 20 MiB 仍能占用无界 CPU/内存/磁盘，或 task 临时目录未在所有终态清理。
