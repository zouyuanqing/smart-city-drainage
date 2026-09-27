# GeoJSON 与空间查询实施摘要

## 范围与 ownership

本轮按工作流确认的冻结合同路径实施 `backend/app/services/hydraulic/geometry.py`，没有创建不存在的 `geo.py`，也没有修改 `backend/app/services/hydraulic/repositories.py`。合同将 `geometry.py` 归 `geo-spatial`、将 `repositories.py` 归 `hydraulic-schema`，并禁止并行模块越界修改（`IMPLEMENTATION_CONTRACT.md:2205-2208, 2255-2271`）。因此本文件是授权的审计摘要，不是 repository owner 的源码变更。

空间后端语义遵循已批准的设计：`postgis` 保留规范 WKT+bbox 并使用派生 typed geometry/GiST；`portable` 只依赖 WKT+bbox，SQL 先粗过滤、服务层再精确判定（`IMPLEMENTATION_CONTRACT.md:1251-1265`）。公共坐标固定 EPSG:4326/lon-lat（`PRD_SUPPLEMENT.md:127-135`）。

## 实际改动

### `backend/app/services/hydraulic/geometry.py`

- 增加可序列化的 `GeometryIssue`、`GeometryValidationReport`、`BoundingBoxValidationReport`、`SpatialFilterReport` 与 `SpatialQueryPlan`（`geometry.py:20-101`）。
- 增加严格二维坐标形状检查：拒绝额外 Z 维度、错误嵌套、NaN/Infinity，并为 Point/LineString/Polygon/MultiPolygon 保留明确错误位置（`geometry.py:104-170`）。
- 增加 WKT 解析、GeoJSON geometry/Feature 解析、FeatureCollection 导入和确定性导出；两条输入路径最终都通过现有 `GeometryDocument` canonical codec（`geometry.py:182-306`）。
- 增加非抛出式几何校验和 fail-fast `GeometryValidationError`；报告区分 `INVALID_COORDINATES`、`CRS_UNKNOWN`、`BBOX_MISMATCH`、`ENTITY_GEOMETRY_MISMATCH`、`INVALID_WKT` 等（`geometry.py:309-379`）。既有 schema 继续提供 WKT/GeoJSON canonical 字段、bbox 一致性和 EPSG:4326 约束（`hydraulic_schemas.py:148-167, 324-560`）。
- 增加 Polygon shell/hole 拓扑校验：零面积、洞越界/相交、洞重叠均拒绝（`geometry.py:637-687`）。
- 增加 `postgis|portable` 查询计划：PostGIS 生成 `geometry && ST_MakeEnvelope(..., 4326)`；portable 生成数值 bbox 粗过滤并标记需要 Python 精确过滤（`geometry.py:382-429`）。实体表名、列名和排序均来自固定映射，未把客户端值拼入 SQL。
- 增加 portable 文本路径：解析每行 `geometry_wkt`，执行 Point/LineString/Polygon/MultiPolygon 与 bbox 的精确相交判定，并把匹配、排除和坏几何分别返回（`geometry.py:432-486, 690-767`）。
- 增加严格 map bbox 解析/报告：有限数、顺序及 `west < east`、`south < north` 均校验（`geometry.py:489-535`）。相交边界采用闭区间，测试覆盖边界点、跨边界线段、包围 bbox 的多边形和完全落入洞内的 bbox。

### `backend/tests/hydraulic/test_geo_spatial.py`

新增 6 个行为测试，覆盖：

- Point 的 WKT/GeoJSON canonical round-trip 与 bbox；
- LineString/Polygon FeatureCollection 导入导出；
- Point/LineString/Polygon 的 bbox 边界与洞语义；
- NaN、错误 CRS、bbox 不一致、实体类型不匹配、嵌套/3D 坐标、自交和非法洞报告；
- PostGIS 与 portable 查询计划差异；
- 无 PostGIS 的 WKT 文本路径及坏 WKT 错误报告（`test_geo_spatial.py:22-205`）。

## 测试与检查（均为本轮实际执行）

1. Python 3.11 完整 hydraulic 测试目录：

   ```bash
   project='/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage'; PYTHONPATH="$project/backend" uv run --no-project --isolated --python 3.11 --with-requirements "$project/backend/requirements.txt" --with pytest==8.0.1 --with pytest-asyncio==0.23.6 --with aiosqlite==0.20.0 python -m pytest "$project/backend/tests/hydraulic" -q
   ```

   输出：`63 passed, 1 warning in 0.15s`。

2. Python 3.11 空间测试加既有 schema 回归：

   ```bash
   project='/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage'; PYTHONPATH="$project/backend" uv run --no-project --isolated --python 3.11 --with-requirements "$project/backend/requirements.txt" --with pytest==8.0.1 --with pytest-asyncio==0.23.6 --with aiosqlite==0.20.0 python -m pytest "$project/backend/tests/hydraulic/test_geo_spatial.py" "$project/backend/tests/test_hydraulic_schema.py" -q
   ```

   输出：`35 passed, 1 warning in 2.22s`。唯一 warning 是既有 Pydantic `model_version` protected-namespace 提示。

3. 类型检查：

   ```bash
   project='/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage'; "$project/backend/.venv/Scripts/python.exe" -m mypy "$project/backend/app/services/hydraulic/geometry.py"
   ```

   输出：`Success: no issues found in 1 source file`。

4. 格式、导入和 lint：

   ```bash
   project='/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage'; "$project/backend/.venv/Scripts/python.exe" -m flake8 --config "$project/backend/.flake8" "$project/backend/app/services/hydraulic/geometry.py" "$project/backend/tests/hydraulic/test_geo_spatial.py"; "$project/backend/.venv/Scripts/python.exe" -m black --check "$project/backend/app/services/hydraulic/geometry.py" "$project/backend/tests/hydraulic/test_geo_spatial.py"; "$project/backend/.venv/Scripts/python.exe" -m isort --check-only --settings-path "$project/backend/pyproject.toml" "$project/backend/app/services/hydraulic/geometry.py" "$project/backend/tests/hydraulic/test_geo_spatial.py"
   ```

   输出：Flake8/isort 无输出且 exit 0；Black 输出 `2 files would be left unchanged`。

## 未完成项、风险与 typed handoff

- **Repository 集成未修改（本轮明确阻断）**：`repositories.py` 的 owner 是 `hydraulic-schema`（`IMPLEMENTATION_CONTRACT.md:2269-2271`）。后续 owner 应消费 `build_spatial_query_plan(...)`：PostGIS 直接执行 `where_sql`；portable 先执行数值 bbox 粗查询，再对返回行调用 `filter_rows_by_bbox(...)`，并保持固定 `stable_key ASC, id ASC` 排序。不得在运行时静默切换 profile；profile/schema 不匹配应 fail closed（`PRD_SUPPLEMENT.md:127-135`, `IMPLEMENTATION_CONTRACT.md:1261-1265`）。
- **未运行 live PostgreSQL/PostGIS 或 portable 数据库查询**：本轮证明的是纯 Python codec、查询计划和文本降级路径；没有连接数据库、执行 GiST/`ST_MakeEnvelope`、或验证真实 migration。
- **未实现 durable import/export/API/job 生命周期**：GeoJSON FeatureCollection codec 已提供，但 20 MiB quarantine、durable network job、流式 export、ETag/manifest 绑定不在本模块 ownership 内。
- **性能未宣称**：纯 Python 精确相交是正确性降级路径，不宣称满足 5000/10000 规模 P95；固定规模 benchmark 尚未执行。
- **并行工作树**：仓库中已有其他 agent 的未提交 hydraulic/schema/依赖改动；本轮未覆盖或清理它们，也未提交/推送任何 commit。

## 交付物对应关系

- **GeoJSON 映射**：`parse_geojson`、`parse_geojson_feature_collection`、`export_geojson_feature_collection`、`canonical_geojson`。
- **bbox 与几何错误报告**：`parse_query_bbox`、`validate_query_bbox`、`validate_geometry`、`GeometryValidationReport`、`SpatialFilterReport`。
- **双存储路径接口**：`build_spatial_query_plan`、`SpatialQueryPlan`（PostGIS SQL 计划）及 `filter_rows_by_bbox`（portable WKT 精确后过滤）。
