# 简化地表积水模型实施摘要

## 范围与契约依据

本实现对应任务“简化地表积水模型”，仅修改任务声明的两个 owned paths：

- `backend/app/services/hydraulic/surface_flood.py`
- `backend/tests/hydraulic/test_surface_flood.py`

源 PRD 要求纯 Python 简化地表积水降级求解器、按不超过 5 分钟帧序列化积水结果（`docs/hydraulic-twin/SOURCE_PRD.md:20-22`）。冻结补充将 `py_ponding` 定义为预离散单元的 reduced-fidelity 模型，禁止把它宣称为二维水动力真值（`docs/hydraulic-twin/PRD_SUPPLEMENT.md:146-156`）；同时要求强类型 `PondCell`/初始状态/目标清单边界（`docs/hydraulic-twin/PRD_SUPPLEMENT.md:260-269`）。实施契约规定 pond cell、stage-area curve、sewer exchange 与初始状态字段（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:302-356`），并将本文件唯一归属 `surface-flood` owner（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2284-2294`）。现有前端帧契约使用 `ponding.feature_index/depth_m/flow_m3s`（`frontend/src/features/simulation/model/types.ts:292-321`）；本实现保留该字段形状。

## 真实改动

### `backend/app/services/hydraulic/surface_flood.py`

- 新增无数据库、无 FastAPI 依赖的不可变输入/输出模型：`SurfaceFloodConfig`、`FloodCatchment`、`FloodNode`、`FloodPipe`、`FloodDevice`、`CatchmentMapping`、`SurfaceFloodCellState` 与 `SurfaceFloodFrame`（`surface_flood.py:75-430`）。
- 新增显式且可审计的汇水区映射：按 `catchment_id`、汇水区 `device_ids`/出口节点、最后 point-in-polygon 的确定性顺序解析；重叠映射保留 ambiguous 报告，`strict/strict_mapping` 可 fail closed，不使用最近点猜测设备绑定（`surface_flood.py:974-1144`）。
- 新增 `compute_surface_flood`（并提供 `build_surface_flood_frame`、`simulate_surface_flood`、`generate_surface_flood_frame` 兼容别名）：接收节点溢流率/体积/溢流深度、管段 fill/overload/surcharge/capacity、降雨（总量或分汇水区）、设备和上一帧；按 `available = previous + rainfall + mapped inflow - recession` 做体积平衡（`surface_flood.py:1483-1734`）。
- 新增非负/有限数门禁、节点溢流阈值、管段超载阈值、洪涝深度阈值与 reporting threshold；退水时出流受当前可用水量约束，因此不会制造负库容，深度、面积、体积在无雨无源入流时单调不增（`surface_flood.py:76-206,1659-1717,1869-1949`）。
- 新增状态 `dry/surface_flow/accumulating/flooded/receding`、设备最低高程基准、水面高程、来源 ID/类型、汇水区 feature index，以及防御性 JSON/内容 SHA-256 输出（`surface_flood.py:70-72,327-468,1964-1989`）。
- 新增 `SurfaceFloodModel` 顺序帧外观、`recede_surface_flood` 清理 helper、`validate_surface_flood_frame` 和 `assert_monotonic_cleanup`（`surface_flood.py:1750-1851,1869-1955,2001-2028`）。
- 生成的 GeoJSON 固定为 EPSG:4326 `FeatureCollection`，复用并校验现有 canonical geometry 工具，输出 `simplified_extent=true`、`model_fidelity=reduced_fidelity`、`is_authoritative=false`；干单元默认不输出 feature，但保留在状态帧中（`surface_flood.py:654-706,1388-1463`）。

### `backend/tests/hydraulic/test_surface_flood.py`

新增 8 个聚焦测试，覆盖：

- 节点溢流成灾：深度、面积、体积、设备高程、汇水区映射与 GeoJSON（`test_surface_flood.py:76-115`）；
- 无雨退水：深度/面积/体积单调不增、状态为 receding、状态机继续清理（`test_surface_flood.py:118-162`）；
- 节点溢流深度与管段 fill ratio 阈值：等于阈值不产流，超过阈值产流并生成洪水状态（`test_surface_flood.py:164-201`）；
- 自相交 Polygon fail closed、负输入拒绝、显式映射/未映射报告（`test_surface_flood.py:206-241`）；
- 整数坐标、负高程、frame schema/hash/质量标签（`test_surface_flood.py:243-296`）。

## 实际执行的验证命令与结果

所有 pytest/py_compile 命令均在 `smart-city-drainage/backend` 执行。当前可用解释器是 Python 3.14.4；项目目标 Python 3.11 解释器存在，但当前环境未安装 pytest 依赖，因此没有把 3.11 运行结果冒充为已验证。

1. 任务聚焦测试（最终版本）：

```text
$ python -m pytest tests/hydraulic/test_surface_flood.py -q
8 passed, 284 warnings in 0.09s
exit 0
```

2. 全部 hydraulic 回归测试（最终版本）：

```text
$ python -m pytest tests/hydraulic -q
160 passed, 4801 warnings in 1.65s
exit 0
```

3. owned 文件语法与基本格式检查：

```text
$ python -m py_compile app/services/hydraulic/surface_flood.py tests/hydraulic/test_surface_flood.py
exit 0

$ python - <<'PY'
from pathlib import Path
for name in ('app/services/hydraulic/surface_flood.py','tests/hydraulic/test_surface_flood.py'):
    lines=Path(name).read_text(encoding='utf-8').splitlines()
    long=[(i,len(line)) for i,line in enumerate(lines,1) if len(line)>100]
    print(name, 'lines=',len(lines), 'over_100=',long)
PY
app/services/hydraulic/surface_flood.py lines= 2080 over_100= []
tests/hydraulic/test_surface_flood.py lines= 296 over_100= []
exit 0
```

4. 质量工具尝试（未通过安装门禁，不计为 PASS）：

```text
$ python -m black --check app/services/hydraulic/surface_flood.py tests/hydraulic/test_surface_flood.py
C:\Python314\python.exe: No module named black
exit 1

$ python -m flake8 app/services/hydraulic/surface_flood.py tests/hydraulic/test_surface_flood.py
C:\Python314\python.exe: No module named flake8
exit 1

$ python -m mypy app/services/hydraulic/surface_flood.py
C:\Python314\python.exe: No module named mypy
exit 1
```

5. 本次没有运行全后端 pytest、Python 3.11 锁定环境、Alembic/PostGIS/Timescale、Docker、solver worker 或前端构建；这些不属于本纯服务模块的聚焦验收，也没有伪造结果。

## 风险与未完成项

1. 这是 reduced-fidelity screening model，不是二维浅水方程、真实淹没前锋或工程认证模型；输出多边形是汇水区 canonical geometry 的 `simplified_extent`，面积比例由 `max_depth_m` 参考深度计算（`surface_flood.py:1-27,1673-1685`）。
2. 默认 `strict_mapping=False` 会把未映射/歧义来源写入 `CatchmentMapping.unmapped_sources` 与 warnings；接入 worker 时应根据产品策略选择严格模式，不能把 warning 当作已绑定事实（`surface_flood.py:277-325,1647-1657`）。
3. 本任务没有接入 ORM、Timescale frame sink、API 或 worker；调用方需把 `SurfaceFloodFrame.to_dict()`/`geojson` 接到自己的 attempt-scoped 持久化边界。
4. `recede_surface_flood` 需要调用方显式传入与前帧一致的配置；默认配置仅适用于通用 screening policy。
5. Black/Flake8/Mypy 未执行成功是环境依赖缺口，不是代码通过证据；应在锁定 Python 3.11 环境补跑并保存结果。

未提交、未推送任何 commit。
