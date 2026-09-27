# 拓扑校验与种子生成实施报告

## 范围与依据

本模块仅实现任务拥有的三个代码/测试文件：

- `backend/app/services/hydraulic/topology.py`
- `backend/app/services/hydraulic/seed.py`
- `backend/tests/hydraulic/test_topology_seed.py`

需求依据：

- FR-1 要求校验悬空、孤立子图、逆坡、重复、缺坐标、环路并生成绑定现有上海设备的树环混合种子：`docs/hydraulic-twin/SOURCE_PRD.md:14-18`。
- 拓扑分级语义规定 `MISSING_COORDINATES`、`DANGLING_NODE`、`DUPLICATE_SEGMENT`、`SELF_LOOP` 为 error；`DISCONNECTED_COMPONENT`、`CYCLE_DETECTED` 为 warning；环路只报告不自动删除：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:318-333`。
- seed 必须是版本化、确定性的，并且设备映射只生成 candidate，需显式确认后才 active：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:467-487,538-545`。
- 上海演示网络边界和计数示例位于 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1538-1558`；现有八个上海设备坐标来自 `backend/app/services/mock_data_generator.py:23-89`。

## 实际改动

### 1. 结构化拓扑报告

`backend/app/services/hydraulic/topology.py` 新增：

- 冻结输入 DTO：`TopologyNode`、`TopologyPipe`；
- 稳定 issue code/severity 枚举：`IssueCode`、`IssueSeverity`（`topology.py:27`）；
- 每条 issue 包含 `severity/code/entity_type/entity_key/message/details`；
- `ValidationReport` 输出 `valid|valid_with_warnings|invalid`、`simulation_ready`、counts、components、cycles、issues、版本号和规范 JSON/hash（`topology.py:108`）；
- 环检测返回确定性 `CycleFinding`，包含环序号、节点与管段 key（`topology.py:92`）；
- `validate_topology()` 检测缺坐标、悬空引用、自环、无向重复管段、逆坡、孤立子图和环路（`topology.py:296`）；
- 输入输出均按 stable key 排序，报告 canonical JSON 使用 sort key、固定分隔符和 SHA-256。

逆坡检测优先使用管段自身上下游高程，缺省时回退到端点节点高程。重复与环检测按无向物理连接判断，因为反向的同一对端点仍是同一物理管段。

### 2. N/M/K 确定性种子

`backend/app/services/hydraulic/seed.py` 新增：

- `SeedConfig`（`seed.py:33`）：N=分支数，M=每分支节点数，K=额外环路管段数；默认上海范围为 `[121.30,31.15,121.60,31.32]`；
- 规模约束：节点数 `N*M+1`，管段数 `N*M+K`，`N>=1`、`M>=2`、`0<=K<=N-1`；
- 使用局部 `random.Random(random_seed)`，不使用全局随机源；
- 固定 stable key、确定性节点/管段排序、Haversin 长度、版本号和 canonical content hash；
- 生成前验证配置，生成后再次调用拓扑校验，并要求实际 fundamental cycle 数严格等于 K（`seed.py:461`）。

### 3. 上海设备候选绑定

生成器接受已有设备投影，并按球面距离选择最近候选节点（`seed.py:416`）。输出 `CandidateDeviceBinding`（`seed.py:176`）固定：

- `target_type="node"`；
- `mapping_version=1`；
- `active=False`；
- 包含设备 id/code、观测类型、目标 stable key 和距离。

因此模块不会按几何最近点“替代拓扑”，也不会自动确认设备绑定；是否 active 由持久层/operator/admin 显式确认流程决定。

## 测试覆盖

`backend/tests/hydraulic/test_topology_seed.py` 包含：

- 参数化独立场景：悬空、孤立子图、逆坡、重复、缺坐标、环路（`test_topology_seed.py:116`）；
- 报告 canonical JSON/hash 与输入顺序无关（`test_topology_seed.py:137`）；
- N/M/K 生成树环混合网络（`test_topology_seed.py:163`）；
- 同随机种子、设备输入顺序变化仍逐字节一致（`test_topology_seed.py:179`）；
- 上海设备只生成 inactive candidate（`test_topology_seed.py:193`）；
- 非法 N/M/K 配置拒绝（`test_topology_seed.py:222`）。

## 本轮执行命令与结果

### Focused pytest

命令：

```bash
cd smart-city-drainage/backend
python -m pytest tests/hydraulic/test_topology_seed.py -q
```

结果：exit 0，`13 passed, 221 warnings in 0.04s`。警告来自当前 Python 3.14 环境中的 `pytest-asyncio` 弃用提示，不是本模块失败。

### 编译与 diff 检查

命令：

```bash
python -m py_compile app/services/hydraulic/topology.py app/services/hydraulic/seed.py tests/hydraulic/test_topology_seed.py
```

结果：exit 0。

```bash
git diff --check -- backend/app/services/hydraulic/topology.py backend/app/services/hydraulic/seed.py backend/tests/hydraulic/test_topology_seed.py
```

结果：exit 0，无输出。

另运行了纯 Python 行长检查，三个 owned 文件均无超过 88 字符的行。

### 同种子实证

命令：

```bash
python -c "from app.services.hydraulic.seed import SeedConfig,generate_tree_loop_seed; a=generate_tree_loop_seed(SeedConfig(n=4,m=5,k=3,random_seed=20260924)); b=generate_tree_loop_seed(SeedConfig(n=4,m=5,k=3,random_seed=20260924)); print(a.content_sha256); print(b.content_sha256); print(a.canonical_json()==b.canonical_json()); print(len(a.nodes),len(a.pipes),a.validation.cycle_count,a.validation.simulation_ready)"
```

输出：

```text
229d88be1f9e342df38a2584f43a439ccb431c8bb2639161c49aec2241ee51ed
229d88be1f9e342df38a2584f43a439ccb431c8bb2639161c49aec2241ee51ed
True
21 23 3 True
```

## 风险与未执行项

1. 当前运行环境是 Python 3.14.4，而仓库契约目标是 Python 3.11；focused pytest 在 3.14 通过，但尚未在冻结的 Python 3.11 环境复跑。
2. 本轮尝试执行 `black/isort/flake8/mypy` 的 check 命令，但当前解释器未安装这些模块，四条命令均 exit 1（`No module named ...`），因此不能记为通过。
3. 本模块只提供纯服务层 DTO/算法，没有修改 API、ORM、migration、durable network-job worker 或持久化候选绑定；这些集成由相应 owner 完成。
4. 本轮没有运行全量 pytest、PostgreSQL/PostGIS migration、网络 job、solver、性能门禁或 live device binding；这些不在本任务 ownedPaths，且无运行证据，不能宣称通过。
5. `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:544-545` 另要求固定 `network_jobs/v1` golden fixtures；该目录不在本任务允许修改路径，本轮未创建。
