# 降雨情景与雨源实施摘要

## 范围与契约依据

本实现对应任务“降雨情景与雨源”，交付设计雨型、自定义校验、随机雨型、`RainfallSource` 抽象和情景复制语义。需求基线要求 1/2/3/5/10/20/50/100 年重现期、1/5/15 分钟步长、最长 72 小时自定义雨型和可复现随机源（`docs/hydraulic-twin/SOURCE_PRD.md:18`）。冻结补充语义要求重现期集合、步长、`floor(duration/step)+1` 样本数、source/version/seed/algorithm/hash 固定，以及被引用情景不可原地修改（`docs/hydraulic-twin/PRD_SUPPLEMENT.md:292-299`）。实现契约进一步固定了 `RainfallSource.materialize` 形状和 `RainfallSeries` 必须携带的元数据（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1299-1311`），并规定本文件由 rainfall owner 负责（`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2255-2276`）。

已有 schema 已提供 `RainfallScenarioKind`、重现期枚举、点序列和基础网格约束（`backend/app/schemas/hydraulic_schemas.py:65-80,655-703`）；本实现保持该持久化词汇，不把数据库 ORM 或 legacy mock generator 引入纯雨源服务。

## 真实改动

### `backend/app/services/hydraulic/rainfall.py`

- 新增冻结常量、枚举和不可变请求/样本/序列模型：允许步长为 `60/300/900` 秒、允许重现期为 `1/2/3/5/10/20/50/100`、最大持续时间为 72 小时（`rainfall.py:28-38,45-72`）。
- 新增 `RainfallRequest`、`RainfallSeries`、`RainfallSample` 和 `CatchmentAllocation`，统一校验时区、有限数、非负强度、严格递增时间网格、样本数、面积/权重和 canonical SHA-256（`rainfall.py:410-447,501-622,705-973`）。
- 新增 `RainfallSource` Protocol 以及唯一内置 `SyntheticRainfallSource`；序列保留 `source_id/source_version/random_seed/algorithm/algorithm_version/content_sha256`（`rainfall.py:975-1169`）。
- 新增设计雨型：固定 Chicago-like screening envelope，按重现期线性缩放，保证相同时间网格下重现期和总量单调（`rainfall.py:1172-1196`）。
- 新增自定义雨型：校验最长 72h、`floor(duration/step)+1` 样本数、起止时间/步长、非负有限强度和汇水区面积分配（`rainfall.py:410-447,1198-1237`）。
- 新增固定 SplitMix64 伪随机序列；相同 seed、请求和算法版本产生逐字节相同 canonical JSON/hash（`rainfall.py:1000-1029,1239-1276`）。
- 新增 `RainfallScenario`、物化和复制语义：复制生成新 UUID，`version + 1`，保留 lineage root、seed、source/algorithm、序列和内容 hash；对象不可变（`rainfall.py:1296-1447,1458-1603`）。
- 保留兼容别名和 mapping 输入适配（`generate_design_storm`、`generate_random_pattern`、`copy_rainfall_scenario`、小时/分钟字段、`seed`/`return_period` 等），降低跨模块接入成本（`rainfall.py:1032-1059,1619-1665`）。

### `backend/tests/hydraulic/test_rainfall.py`

新增 27 个聚焦测试，覆盖：

- 1–100 年重现期单调性及非法重现期；
- 1/5/15 分钟步长约束；
- 72 小时上限、`floor(duration/step)+1` 和非整除持续时间；
- NaN/Inf/负强度、时间网格、汇水区面积；
- 固定 seed 的 canonical hash/样本复现及不同 seed 区分；
- `RainfallSource` 身份、hash、总量；
- scenario 新 ID、root/seed/version/points 保留、ID 冲突和 `.copy()` lineage 保留（`test_rainfall.py:31-328`）。

## 实际执行的验证命令与结果

所有以下命令均在 `smart-city-drainage/backend` 执行；Python 版本按项目锁定到 3.11。

1. 聚焦测试（任务要求的模块测试规模）：

```text
$ C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest tests/hydraulic/test_rainfall.py -q
27 passed, 1 warning in 0.21s
exit 0
```

2. 全部 hydraulic 测试（回归检查，不替代上面的聚焦测试）：

```text
$ C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest tests/hydraulic -q
66 passed, 1 warning in 0.13s
exit 0
```

3. 质量检查（同一批 owned 文件）：

```text
$ C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -m black --check app/services/hydraulic/rainfall.py tests/hydraulic/test_rainfall.py
All done! ✨ 🍰 ✨
2 files would be left unchanged.
exit 0

$ C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -m isort --check-only --profile black app/services/hydraulic/rainfall.py tests/hydraulic/test_rainfall.py
exit 0

$ C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -m flake8 app/services/hydraulic/rainfall.py tests/hydraulic/test_rainfall.py
exit 0

$ C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -m mypy --follow-imports=skip app/services/hydraulic/rainfall.py
Success: no issues found in 1 source file
exit 0
```

4. 协议/确定性 smoke check：

```text
$ ... python -B -c "from app.services.hydraulic.rainfall import *; ..."
{'protocol': True, 'design_samples': 13, 'design_depth': 333.5583847733, 'seed_hash_equal': True, 'seed_hash': 'c48f31467411f6d0b2e6e57af3b7c80feff969bbad5d565581e6127b868836d6'}
exit 0
```

5. 全后端 pytest 也实际运行过，但不是本模块的通过声明：

```text
$ C:/Python314/Scripts/uv.EXE run --isolated --no-project --python 3.11 --with-requirements requirements-py311.lock python -B -m pytest -q
209 passed, 4 skipped, 5 failed, 1 warning in 21.08s
exit 1
```

5 个失败均在本任务 ownedPaths 之外：两个 migration gate 测试看到已存在的 `backend/alembic/versions/0002_hydraulic_schema.py` 路径变化，两个 dependency handoff 测试报告 `.env.example` hash 不匹配，一个 gate 测试报告并行工作区已出现 `frontend/performance/run-twin-map.mjs`。本任务没有修改这些文件，也没有把全后端结果写成 PASS。

## 风险与未运行项

1. 本模块是纯服务层；Rainfall CRUD/API、ORM repository、worker materialization 和 simulation 接线不在本任务 ownedPaths，需由后续 owner 接入。
2. 设计雨型是固定、可审计的 synthetic screening envelope，不替代当地 IDF/暴雨公式或工程认证。
3. `total_depth_mm` 按“样本值代表其后续 interval 的起点”积分；终点样本不产生额外 interval，非整除持续时间保留请求的 `end_at` 并按 floor 规则生成点列。
4. 未运行 live PostgreSQL/PostGIS/Timescale、Alembic upgrade、Docker、solver 或前端集成；这些不由本纯 Python 模块测试覆盖。
5. 全后端测试的 5 个 gate 失败需要 gate/integration owner 处理；本任务按 ownedPaths 约束未修复或覆盖相关文件。
