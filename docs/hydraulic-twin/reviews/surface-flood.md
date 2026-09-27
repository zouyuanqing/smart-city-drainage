# 简化地表积水模型独立审查

- 模块：`surface-flood`（简化地表积水模型）
- 审查日期：2026-09-25
- 审查方式：只读核对冻结契约、真实实现、聚焦测试和小型定向诊断；未修改实现、测试或契约代码。
- 结论：**NOT APPROVED / `approved=false`**。指定 focused 测试通过，但存在多项高风险契约/状态一致性问题。

## 1. 审查范围与冻结依据

本次任务规格要求：根据节点溢流、管段超载、汇水区和设备高程生成积水深度、面积、GeoJSON 帧，并保证非负与无雨单调清理；交付物包括地表积水状态、深度/面积/多边形和汇水区映射（任务规格原文）。

实际读取并核对了：

- `backend/app/services/hydraulic/surface_flood.py`
- `backend/tests/hydraulic/test_surface_flood.py`
- `docs/hydraulic-twin/implementation/surface-flood.md`
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md`
- `backend/app/services/hydraulic/geometry.py`（确认几何 canonical/校验边界）

冻结契约中与本模块直接相关的条款：

- `IMPLEMENTATION_CONTRACT.md:305-326`：`PondCell` 必须有 stage-area curve、cell links、sewer exchange；`IMPLEMENTATION_CONTRACT.md:411-418,438` 要求这些字段闭合、引用存在、曲线严格递增。
- `IMPLEMENTATION_CONTRACT.md:770-814`：持久化 frame 必须绑定 `attempt_id`、`simulation_id`、solver、target manifest、质量状态，并以 attempt-scoped staging 发布。
- `IMPLEMENTATION_CONTRACT.md:92-106`：hash 必须来自 canonical semantic content，结果应可复现。
- `PRD_SUPPLEMENT.md:347-360`：frame 状态必须有限且非负；超过容差的负 depth/volume 必须失败，不能用 `max(0, x)` 静默修复；还要求质量台账。

## 2. 实际执行的检查

### 2.1 任务指定的 focused pytest（Python 3.11 锁定环境）

命令：

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && /c/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest tests/hydraulic/test_surface_flood.py -q
```

实际输出：

```text
........                                                             [100%]
8 passed, 1 warning in 0.06s
```

这证明当前 8 个行为测试在 Python 3.11 锁定依赖下通过，不证明下述未覆盖的失败边界或完整 worker/数据库契约。

### 2.2 当前解释器 focused pytest

命令：

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && python --version && python -m pytest tests/hydraulic/test_surface_flood.py -q
```

实际输出（Python 3.14.4）：

```text
........                                                             [100%]
8 passed, 284 warnings in 0.05s
```

### 2.3 语法编译

命令：

```text
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && python -m py_compile app/services/hydraulic/surface_flood.py tests/hydraulic/test_surface_flood.py
```

实际结果：exit 0，无输出。

### 2.4 定向诊断

以下命令均在 `smart-city-drainage/backend` 执行，使用 `python`（Python 3.14.4）。诊断只读，不写仓库文件。

#### D1：跨汇水区管段映射

命令：`cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && python - <<'PY'` 后执行构造两个不相交 Polygon、`N-1 -> N-2` 管段、`strict_mapping=True` 和 `pipe_overload_ratio=2.0` 的 inline probe。

实际输出：

```text
pipe_map {'P-1': 'C-2'} ambiguous_pipes () depths [0.0, 0.01] source_ids [(), ('pipe:P-1',)] complete True
```

#### D2：mapping 形式的 previous frame 绕过顺序检查

命令：同一 `cd ... && python - <<'PY'` 后先构造 `frame_index=7` 的 `SurfaceFloodFrame`，再把 `first.to_dict()` 作为 `previous_frame`，请求 `frame_index=0`。

实际输出：

```text
accepted_frame_index 0 accepted_valid_at 2026-01-01T00:00:00+00:00 prior_depth_used 0.2
```

#### D3：负水面高程被公开 helper 静默归零

命令：同一 `cd ... && python - <<'PY'` 后执行 `calculate_flood_depth(-1.0, 0.0)`。

实际输出：

```text
negative_surface_result 0.0
```

#### D4：冻结 dataclass 的公开 GeoJSON 可改写 hash

命令：同一 `cd ... && python - <<'PY'` 后生成有 feature 的 frame，修改 `frame.geojson["features"][0]["properties"]["depth_m"]`，前后调用 `content_sha256()`。

实际输出：

```text
hash_changed_after_public_mutation True mutated_depth -999.0
```

#### D5：悬空 outlet/device 引用被接受

命令：同一 `cd ... && python - <<'PY'` 后传入 `outlet_node_key="N-MISSING"`、`device_ids=["D-MISSING"]`，不提供对应节点/设备。

实际输出：

```text
accepted_dangling_refs True reference_elevation_m 0.0 mapping_complete True mapping_warnings ()
```

#### D6：冻结 pond-cell 字段被静默忽略

命令：同一 `cd ... && python - <<'PY'` 后向 catchment mapping 传入 `stage_area_curve`、`cell_links`、`sewer_exchange_links`、`roughness_manning_n` 和 `depression_storage_m`，再输出 cell 序列化键。

实际输出：

```text
accepted_extra_pond_fields True depth_m 0.1 area_m2 100.0 total_area_m2 1000.0 serialized_keys ['area_m2', 'catchment_id', 'depth_m', 'device_elevation_m', 'device_ids', 'feature_index', 'flooded_area_m2', 'inflow_m3', 'outflow_m3', 'rainfall_m3', 'reference_elevation_m', 'simplified_extent', 'source_ids', 'source_types', 'stable_key', 'status', 'threshold_exceeded', 'total_area_m2', 'volume_m3', 'water_surface_elevation_m']
```

#### D7：cleanup helper 丢失设备和映射信息

命令：同一 `cd ... && python - <<'PY'` 后生成带设备 `D-1` 的 frame，再调用 `recede_surface_flood`。

实际输出：

```text
first_device_ids ('D-1',) first_device_elev 9.5
second_device_ids () second_device_elev None
```

#### D8：字符串 `"false"` 被当作 surcharge=True

命令：同一 `cd ... && python - <<'PY'` 后传入 `surcharged="false"`、`fill_ratio=0`，阈值为 1。

实际输出：

```text
string_false_accepted True depth_m 0.0010000000000000009 source_ids ('pipe:P-1',)
```

#### D9：frame validator 接受缺字段的 FeatureCollection

命令：同一 `cd ... && python - <<'PY'` 后用 `dataclasses.replace` 将合法 frame 的 `geojson` 替换为仅含 `{"type": "FeatureCollection"}` 的 mapping，再调用 `validate_surface_flood_frame`。

实际输出：

```text
validator_accepted_missing_features True geojson_keys ['type'] cell_depth 0.1
```

#### D10：本地 frame 缺少冻结发布字段

命令：同一 `cd ... && python - <<'PY'` 后生成一帧并检查 `to_dict()` 键。

实际输出：

```text
frame_keys ['catchment_mapping', 'cells', 'dt_seconds', 'frame_index', 'geojson', 'is_authoritative', 'model_fidelity', 'model_version', 'network_id', 'network_revision_id', 'ponding', 'rainfall_mm', 'schema_version', 'valid_at']
required_contract_fields_present {'attempt_id': False, 'simulation_id': False, 'solver_id': False, 'solver_version': False, 'target_manifest_id': False, 'target_manifest_sha256': False, 'quality_status': False, 'mass_residual_relative': False}
```

## 3. Findings

### H-01（高）：实现不是冻结契约要求的 `PondCell` 物理模型，stage-area/link/sewer exchange 被静默丢弃

**证据：**

- `backend/app/services/hydraulic/surface_flood.py:209-218` 的 `FloodCatchment` 只有 `area_m2`、elevation、device IDs 等字段，没有 `stage_area_curve`、cell links、sewer exchange links、roughness 或 depression storage。
- `backend/app/services/hydraulic/surface_flood.py:1656-1685` 直接用 `rain_m3 + inflow - recession` 和 `catchment.area_m2 * min(1, depth/max_depth)` 计算面积/库容；没有 stage-area 插值、单元间交换或 sewer transfer ledger。
- 冻结契约 `IMPLEMENTATION_CONTRACT.md:305-326,411-418,438` 明确要求上述 PondCell 字段、曲线严格递增、引用存在和 exchange schema。
- D6 明确显示传入这些字段不会报错，但它们不出现在输出中，面积仍按固定总面积计算。

**影响：** 汇水区/积水单元的真实水面面积变化、单元间传播和管网-地表交换无法表达；调用方可能误以为这些冻结字段已被消费。该缺口还使质量台账无法证明内部 transfer 不重复计数。

**建议：** 在强类型 `PondCellSnapshot`/domain 层 materialize 后再进入本服务；实现 stage-area 插值、方向性 links、sewer exchange 和 transfer ledger。若本文件明确只做 pre-adapter screening，则必须在接口/命名/发布门禁中禁止把它标为冻结的 `py_ponding` 实现，而不能静默接受并忽略契约字段。

### H-02（高）：跨汇水区管段被静默归到下游，`strict_mapping=True` 也不会拒绝歧义

**证据：**

- `backend/app/services/hydraulic/surface_flood.py:983-988` 声称 ambiguous records 会被保留；但 `surface_flood.py:1088-1107` 只取 `downstream_node_key` 的映射，找不到时才取 upstream，从不比较两个端点的不同 catchment，也没有向 `ambiguous_pipes` 写入。
- `CatchmentMapping` 明明有 `ambiguous_pipes` 字段（`surface_flood.py:286-288`），但该路径不会填充。
- D1 在 `strict_mapping=True` 下仍输出 `pipe_map {'P-1': 'C-2'}`、`ambiguous_pipes ()`，并把 0.01 m³ 源水全部放入 C-2；`complete=True`。

**影响：** 跨越汇水区边界的管段超载会被错误归属，设备风险和积水面积可能落在错误区域；严格模式也无法阻止错误结果发布。

**建议：** 收集上下游所有匹配项；不同 catchment 时标记 `ambiguous_pipes` 并在 strict 模式抛出结构化错误；没有唯一归属时要求显式 `catchment_id` 或拆分 transfer。补充跨区、环向和上下游冲突 fixture。

### H-03（高）：previous state 和 initial frame 没有强制的 frame/revision/config 身份，状态可跨版本拼接

**证据：**

- `backend/app/services/hydraulic/surface_flood.py:1176-1205` 接受任意 `Mapping` 作为 previous state；`surface_flood.py:1541-1563` 只有在 previous 是 `SurfaceFloodFrame` 对象时才检查 frame index/time 单调，mapping 形式被完全跳过。
- `SurfaceFloodModel.__init__` 在 `surface_flood.py:1758-1778` 直接保存传入 `initial_frame`，没有比较 network/revision、catchment 集合、geometry/area 或 config。
- D2 将完整 frame 的 `to_dict()` 作为 previous，却成功接受 `frame_index=0`（原 frame 为 7）。
- D6 的 model 诊断显示 `model_revision=revision-model`、`initial_revision=local`，仍接受该 initial frame 并继续输出状态。

**影响：** worker 从 JSON/数据库恢复、调用方传入 raw state 或测试重放时，可以把不同 revision、不同 target manifest 或不同时间网格的库容拼在一起；`NON_MONOTONIC_FRAME` 和 durable attempt fencing 语义被绕过。

**建议：** mapping previous 若包含 frame metadata，必须走同一 frame validator；无论对象形式如何都校验 `network_id + network_revision_id + target_manifest`、完整 catchment 集合、config fingerprint、frame index/time。`SurfaceFloodModel` 初始化时 fail closed，不要静默继承不匹配 frame。

### H-04（高）：悬空 outlet/device 引用不报错且被伪装成完整映射，导致错误高程基准

**证据：**

- `backend/app/services/hydraulic/surface_flood.py:776-802` 只规范化 `outlet_node_key` 和 `device_ids` 的字符串，不检查节点/设备是否在输入集合中；`surface_flood.py:918-927` 也不检查 `device.node_key` 是否存在。
- `_reference_elevation` 在 `surface_flood.py:1320-1328` 找不到指定 outlet 时静默回退到 `0.0`。
- D5 传入 `N-MISSING`/`D-MISSING` 后得到 `mapping_complete=True`、无 warning、reference elevation `0.0`。

**影响：** 一个拼写错误或跨 revision 引用会静默改变水面高程和设备风险判断，并让 `complete=True` 误导下游认为绑定已闭合。

**建议：** normalization 后建立 node/device key 集合，拒绝所有悬空引用；只有确实没有可选 outlet 时才允许缺省，并显式区分“无 outlet”与“引用不存在”。`complete` 必须在悬空 device/outlet 存在时为 false。

### H-05（高）：本地 `SurfaceFloodFrame` 不能作为冻结 simulation frame 发布

**证据：**

- `backend/app/services/hydraulic/surface_flood.py:390-403,445-461` 的 frame 只包含本地 `frame_index`、时间、cells、mapping 和 GeoJSON；没有 `attempt_id`、`simulation_id`、`solver_id/version`、target manifest ID/hash、`quality_status` 或质量残差。
- 冻结 frame 表要求这些字段并绑定 attempt/fencing：`IMPLEMENTATION_CONTRACT.md:770-814`。
- D10 逐一检查这些字段，结果全部为 `False`。
- 实现摘要 `docs/hydraulic-twin/implementation/surface-flood.md:89-95` 也明确承认尚未接入 ORM、Timescale frame sink、API 或 worker。

**影响：** 该对象可以作为本地展示投影，但不能证明结果属于哪个 attempt/revision/solver，也不能满足质量门禁、幂等写入和 published pointer 语义。若将它直接接到结果 API/worker，会产生无法审计或错误发布的帧。

**建议：** 明确把本模块标为 domain-only adapter，并由 integration 层构造 attempt-scoped `SimulationFrame`/sink payload；补充 manifest、solver、quality/mass residual 和 fencing 集成测试。若本轮交付目标就是完整 `py_ponding` frame，则当前实现不能批准。

### M-01（中）：公开深度 helper 用 `max(0, x)` 静默修复超容差负状态

**证据：**

- `backend/app/services/hydraulic/surface_flood.py:1964-1972` 的 `calculate_flood_depth` 对 `surface-reference` 直接取 `max(0.0, ...)`，没有误差阈值、错误码或 correction 记录。
- D3 对水面高程 -1 m、基准 0 m 返回 `0.0`，没有失败。
- `PRD_SUPPLEMENT.md:347` 明确规定超过数值容差的负 depth 必须失败，禁止 `max(0, x)` 静默修复。

**影响：** 非法 frame 或错误高程输入会被伪装成 dry，掩盖数据/求解器错误；该 helper 还以 `flood_depth_m` 别名公开，调用方容易把它当作安全校验。

**建议：** 区分合法 dry 状态和非法负状态；仅对明确的微小数值误差归零并记录 correction，超阈值抛 `INVALID_STATE`/数值失败。

### M-02（中）：`frozen=True` 只提供浅层冻结，GeoJSON 和 mapping 可在 hash 后被改写

**证据：**

- `SurfaceFloodCellState.geometry` 在 `surface_flood.py:346`、`SurfaceFloodFrame.geojson` 在 `surface_flood.py:399` 以 `Mapping` 暴露，实际值仍可变 dict；`CatchmentMapping` 的映射字段同样未 deep-freeze（`surface_flood.py:279-282`）。
- `content_sha256()` 在 `surface_flood.py:463-464` 每次从当前对象重新计算，而不是从不可变 canonical payload 计算。
- D4 修改公开 `geojson` 后 hash 从旧值变为新值，且被改写为 `-999.0`。

**影响：** 调用方可以在验证、hash、持久化之间修改几何/属性，破坏结果可复现性和审计一致性；`json.dumps(..., allow_nan=False)` 的保护也可被绕过在 hash 之前。

**建议：** 内部保存不可变 canonical JSON/深冻结结构，公开访问返回 defensive copy；hash 只从内部不可变 payload 生成，并增加“外部修改不影响 frame/hash”的测试。

### M-03（中）：`validate_surface_flood_frame` 对 GeoJSON 只做浅检查，缺字段的 FeatureCollection 仍可“验证通过”

**证据：**

- `backend/app/services/hydraulic/surface_flood.py:1939-1949` 只检查顶层 `type == FeatureCollection` 和每个已有 feature 的 geometry；没有要求 `features` 为数组、feature 数量与 cells 一致、feature properties/CRS/bbox/scope 完整。
- D9 用仅含 `type` 的 mapping 替换 GeoJSON 后，`validate_surface_flood_frame` 仍正常返回。
- 同一验证器还没有检查 `device_elevation_m`、frame 时间/dt、mapping scope 等 frame-level 不变量。

**影响：** 任意调用方可以构造一个带合法 cell geometry、但没有可消费 GeoJSON feature/属性的 frame，并把它交给后续 sink；生成路径目前恰好完整，验证边界却没有 fail closed。

**建议：** 复用项目 canonical GeoJSON schema，强制 FeatureCollection 的 CRS/bbox/features/properties/scope，检查 wet cell 与 feature 一一对应、feature index/geometry/catchment key 一致；补 malformed frame golden tests。

### M-04（中）：`recede_surface_flood` 重建输入时丢失设备映射和高程，清理帧不能保持同一业务语义

**证据：**

- `backend/app/services/hydraulic/surface_flood.py:2013-2021` 只从旧 cell 重建 `stable_key/area/elevation/geometry`，没有 nodes、devices、outlet 或原 mapping；`surface_flood.py:2022-2033` 随后用空设备集合计算下一帧。
- D7 显示第一帧 `device_ids=('D-1',), device_elevation_m=9.5`，清理帧变成 `device_ids=(), device_elevation_m=None`。
- `config` 也没有存储在 frame 中；省略时 `surface_flood.py:2028` 使用默认退水率，而不是可证明的上一帧 policy。

**影响：** 清理阶段的设备暴露状态和汇水区映射发生回归，时间轴/设备风险消费者无法把前后帧视为同一状态；调用方漏传 config 时退水速率也会静默改变。

**建议：** frame 保留不可变 config fingerprint 和完整 normalized definitions，或让 cleanup 接受并校验原 `SurfaceFloodModel`/snapshot；至少继承并输出 device mapping/elevation，未匹配配置必须拒绝而不是使用默认值。

### M-05（中）：布尔字段用 `bool(value)` 强制转换，字符串 `"false"` 会制造洪水源

**证据：**

- `backend/app/services/hydraulic/surface_flood.py:850` 和 `surface_flood.py:896` 对 `surcharged` 使用 `bool(...)`，没有要求输入必须是 bool。
- D8 传入 `surcharged="false"`，在 fill ratio 为 0、阈值为 1 时仍生成 `source_ids=('pipe:P-1',)` 和正深度。
- 冻结强类型约束要求 closed/typed fields，未知或错误类型不能靠 Python truthiness 解释：`IMPLEMENTATION_CONTRACT.md:436-438`。

**影响：** 上游 JSON/消息解码错误会被解释为“已超载”，产生假积水；反向的 truthy/falsey 字符串也会影响告警和 GeoJSON 状态。

**建议：** 只接受 `bool`，对字符串/数字/None 返回结构化 `INVALID_TYPE`；增加 false/true、字符串、数字和缺字段的矩阵测试。

## 4. 测试覆盖与未执行项

现有 `backend/tests/hydraulic/test_surface_flood.py:75-296` 的 8 个测试覆盖了正常溢流、无雨退水、阈值、自相交 geometry、负源值、基础设备映射、负高程和基础 hash。尚未覆盖本报告的跨区管段、悬空引用、previous mapping/revision、mutable hash、stage-area/link/exchange、完整 GeoJSON validator、cleanup 元数据、严格 bool 类型和负深度 helper 边界；因此 focused PASS 不能作为完整契约通过证据。

本轮按任务要求没有重复最终全量门禁。以下项目本轮**未运行**，不能据此宣称通过：

- `tests/hydraulic` 全目录和 backend 全量 pytest；
- Python 3.11 下 hydraulic 全量、性能 benchmark；
- PostgreSQL/PostGIS/Timescale、Alembic、worker、FrameSink、Compose；
- 真实设备/网络数据、端到端帧持久化、取消/重试/发布门禁；
- Black/Flake8/Mypy 等质量工具。

## 5. 决策

**`approved=false`。** H-02 至 H-05 已足以阻断交付：跨汇水区来源会错配，状态可跨 revision/frame 拼接，悬空引用会伪造完整映射，且当前 frame 不能满足冻结的 attempt-scoped 发布契约。M-01～M-05 还分别破坏 fail-closed 深度语义、hash 不可变性、GeoJSON 验证、退水帧连续性和输入类型安全。应先修复高风险项并补齐上述回归/集成 fixture，再进行独立复审。
