# 拓扑校验与种子生成独立审查

## 结论

- **审查结果：不通过（`approved=false`）**
- **阻断原因：4 个 high、1 个 medium。** 结构化报告会把非环路边报告成环路；缺失管底高程和孤立分量仍会被标记为 `simulation_ready=true`；合法 N/M/K 配置可稳定生成失败；此外，声明的高程上下界没有被生成器遵守。
- 本次只读取真实实现、冻结契约与测试，并写入本审查报告；未修改任何实现或测试文件。

## 审查依据

- 本次任务给定规格：检测悬空、孤立子图、逆坡、重复、缺坐标、环路，输出 error/warning；生成可配置树环混合网络并绑定上海设备。
- 冻结补充契约规定：`MISSING_INVERT` 为 error 且阻断 simulation-ready，`DISCONNECTED_COMPONENT` 的 simulation-ready 必须阻断：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:327-330`。
- 固定输入/版本必须产生确定性 canonical DTO/report/hash：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:544-545`。
- 实际实现：`backend/app/services/hydraulic/topology.py`、`backend/app/services/hydraulic/seed.py`、`backend/tests/hydraulic/test_topology_seed.py`。
- 实施摘要：`docs/hydraulic-twin/implementation/topology-seed.md`。

## 已执行检查

### 1. 任务指定的 focused pytest

命令：

```bash
cd /c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend && python -m pytest tests/hydraulic/test_topology_seed.py -q
```

输出：

```text
.............                                                            [100%]
13 passed, 221 warnings in 0.04s
```

`python --version` 输出：

```text
Python 3.14.4
```

因此 focused 文件在本会话、Python 3.14.4 下通过；冻结目标 Python 3.11 **未运行**。221 条均为 `pytest-asyncio` 弃用警告。上述通过没有覆盖下列定向失败场景。

### 2. 定向诊断

- 环路成员诊断：1 个真实三节点环被报告为 4 节点/4 边，且包含真实环外的 `P-AB`。
- 环路规模诊断：100 个真实三边环共 300 条实际环边，报告累计 10,300 条环边/节点成员。
- 缺高程诊断：两个节点均无 invert、单管连接，报告 `valid / simulation_ready=True / issues=[]`。
- 孤立分量诊断：两节点相连、第三节点孤立，报告 `valid_with_warnings / simulation_ready=True`。
- 合法配置诊断：`SeedConfig(n=67,m=3,k=66,random_seed=0)` 抛出 `generated seed failed topology validation`，共有 66 个 `REVERSE_SLOPE` error。
- 高程边界诊断：`SeedConfig(n=1,m=2,k=0,random_seed=0)` 生成 `8.0206`，超过声明上限 `8.0`。

具体命令和输出见各 finding。

## Findings

### HIGH-1：所谓 fundamental cycle 不是实际环路，结构化报告包含环外节点/边并可被放大

**证据**

- `path_nodes()` 没有求生成树中两个非树边端点之间的唯一路径，而是把两个端点各自的全部祖先集合做并集：`backend/app/services/hydraulic/topology.py:258-269`。
- 该并集随后直接成为 `CycleFinding.node_keys`，并把这些节点之间的所有邻接边收集为 `pipe_keys`，包括 chord 和环外支路边：`backend/app/services/hydraulic/topology.py:271-289`。
- 现有环路测试只断言出现 `CYCLE_DETECTED`/warning，没有核对成员：`backend/tests/hydraulic/test_topology_seed.py:103-111`；生成测试也只核对 `cycle_count == 3`：`backend/tests/hydraulic/test_topology_seed.py:163-174`。

执行：

```bash
PYTHONPATH=smart-city-drainage/backend python - <<'PY'
from app.services.hydraulic.topology import TopologyNode, TopologyPipe, validate_topology
nodes = [TopologyNode(k, 121.4, 31.2, 3.0) for k in ('A','B','C','D','E')]
pipes = [
    TopologyPipe('P-AB','A','B',3,2),
    TopologyPipe('P-BC','B','C',2,1),
    TopologyPipe('P-BD','B','D',2,1),
    TopologyPipe('P-CD','C','D',1,0),
    TopologyPipe('P-AE','A','E',3,2),
]
report = validate_topology(nodes, pipes)
cycle = report.cycles[0]
print('cycle_count=', report.cycle_count)
print('reported_nodes=', cycle.node_keys)
print('reported_pipes=', cycle.pipe_keys)
print('actual_cycle_nodes=', ('B','C','D'))
print('actual_cycle_pipes=', ('P-BC','P-BD','P-CD'))
PY
```

输出：

```text
cycle_count= 1
reported_nodes= ('A', 'B', 'C', 'D')
reported_pipes= ('P-AB', 'P-BC', 'P-BD', 'P-CD')
actual_cycle_nodes= ('B', 'C', 'D')
actual_cycle_pipes= ('P-BC', 'P-BD', 'P-CD')
```

另一个规模诊断（100 个三边环，共享 100 深度的 trunk）输出：

```text
cycle_count= 100
actual_total_cycle_edges= 300
reported_total_cycle_edges= 10300
actual_total_cycle_nodes= 300
reported_total_cycle_nodes= 10300
```

**影响**

本模块的核心交付物“结构化校验报告”会给出错误环路成员；同一成员还进入 `CYCLE_DETECTED.details` 和 canonical hash。大量环共享深 trunk 时，报告体积会按“环数 × trunk 深度”增长，5000/10000 目标规模下存在明显内存/CPU 放大风险。

**必须修复**

使用生成树深度/LCA 重建端点间唯一树路径，只收集该路径的节点和边；或采用能直接提取简单环的确定性 DFS。增加真实环成员、chord、环外支路和共享深 trunk 的精确断言及规模回归。

### HIGH-2：缺失 invert 不会产生 error，simulation-ready 错误放行

**证据**

- `IssueCode` 没有 `MISSING_INVERT`：`backend/app/services/hydraulic/topology.py:27-34`。
- 节点和管段 invert 字段均允许 `None`：`backend/app/services/hydraulic/topology.py:43-67`。
- 逆坡检查仅在上下游 invert **都存在且有限**时执行，缺失值被静默跳过：`backend/app/services/hydraulic/topology.py:371-397`。
- `simulation_ready` 仅取决于现有 error 数，因此缺高程网络会通过：`backend/app/services/hydraulic/topology.py:443-455`。
- 冻结契约明确要求 `MISSING_INVERT` 为 error，并阻断 simulation-ready：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:327`。

执行：

```bash
# cwd: smart-city-drainage/backend
PYTHONPATH=. python - <<'PY'
from app.services.hydraulic.topology import TopologyNode, TopologyPipe, validate_topology
report = validate_topology(
    [TopologyNode('A',121.4,31.2,None), TopologyNode('B',121.4,31.2,None)],
    [TopologyPipe('P1','A','B')],
)
print('status=', report.status.value)
print('simulation_ready=', report.simulation_ready)
print('issue_codes=', [issue.code.value for issue in report.issues])
PY
```

输出：

```text
status= valid
simulation_ready= True
issue_codes= []
```

**影响**

后续 solver 可收到没有管底高程的“simulation-ready” revision，数值初始化将缺少必要边界/几何输入；确定性 hash 还会把该无效状态稳定地固化。

**必须修复**

增加并结构化输出 `MISSING_INVERT` error；对 simulation-ready 所需的节点/管段 invert 做存在性和有限性校验，并覆盖“全部缺失”“部分缺失”“显式 pipe invert 回退”的测试。导入可保存与 simulation-ready 阻断应分开建模，不能只靠 `error_count`。

### HIGH-3：孤立/断开分量被判为 warning 且仍 simulation-ready

**证据**

- 找到多个连通分量后，代码对所有非最大分量固定发出 warning：`backend/app/services/hydraulic/topology.py:399-418`。
- `simulation_ready` 仍只检查 error 数，warning 不阻断：`backend/app/services/hydraulic/topology.py:443-455`。
- 冻结契约规定 `DISCONNECTED_COMPONENT` 无论导入分级如何，simulation-ready 必须阻断：`docs/hydraulic-twin/PRD_SUPPLEMENT.md:329`。
- 现有测试只断言 disconnected 场景为 warning/`valid_with_warnings`，没有断言 `simulation_ready is False`：`backend/tests/hydraulic/test_topology_seed.py:75-80`、`backend/tests/hydraulic/test_topology_seed.py:115-133`。

执行：

```bash
# cwd: smart-city-drainage/backend
PYTHONPATH=. python - <<'PY'
from app.services.hydraulic.topology import TopologyNode, TopologyPipe, validate_topology
nodes = [
    TopologyNode('A',121.4,31.2,2.0),
    TopologyNode('B',121.4,31.2,1.0),
    TopologyNode('C',121.4,31.2,2.0),
]
pipes = [TopologyPipe('P1','A','B',2.0,1.0)]
report = validate_topology(nodes, pipes)
print('status=', report.status.value)
print('simulation_ready=', report.simulation_ready)
print('issue_codes=', [issue.code.value for issue in report.issues])
PY
```

输出：

```text
status= valid_with_warnings
simulation_ready= True
issue_codes= ['DISCONNECTED_COMPONENT']
```

**影响**

断开分量会进入 solver，可能产生没有可解释边界条件的部分结果；同时 `Network.simulation_ready` 与冻结契约不一致。

**必须修复**

将 import status 与 simulation eligibility 分开计算；任何未被明确建模为合法独立 boundary 的 `DISCONNECTED_COMPONENT` 都必须令 `simulation_ready=False`。测试必须分别覆盖 import warning 和 simulation submit blocking。

### HIGH-4：合法 N/M/K 配置会因随机高程扰动稳定地生成失败

**证据**

- `SeedConfig` 只要求 `N>=1`，没有限制会触发该问题的规模：`backend/app/services/hydraulic/seed.py:55-62`。
- 相邻 branch 的基础 invert 差按 `0.7 * invert_span / (N-1)` 缩小，但每个节点又独立加入 `±0.04 m` 噪声：`backend/app/services/hydraulic/seed.py:305-319`。
- 跨 branch loop 边从 branch `b` 连到 branch `b+1`：`backend/app/services/hydraulic/seed.py:404-418`；生成器发现任何逆坡就整体抛错：`backend/app/services/hydraulic/seed.py:476-486`。
- 现有 N/M/K 成功用例只使用 `N=4`：`backend/tests/hydraulic/test_topology_seed.py:162-174`，没有覆盖配置域边界。

执行：

```bash
PYTHONPATH=smart-city-drainage/backend python - <<'PY'
from app.services.hydraulic.seed import SeedConfig, generate_tree_loop_seed
for n in (67, 80, 100):
    for seed in range(1000):
        cfg = SeedConfig(n=n, m=3, k=n-1, random_seed=seed)
        try:
            generate_tree_loop_seed(cfg)
        except RuntimeError as exc:
            print(f'n={n} seed={seed} error={exc}')
            break
    else:
        print(f'n={n} no_failure_in_1000_seeds')
PY
```

输出：

```text
n=67 seed=0 error=generated seed failed topology validation
n=80 seed=0 error=generated seed failed topology validation
n=100 seed=0 error=generated seed failed topology validation
```

进一步检查 `n=67,m=3,k=66,seed=0` 得到 `errors=66`，首个错误为：

```text
REVERSE_SLOPE SH-P-00202
upstream SH-N-000-001 = 7.0432
downstream SH-N-001-000 = 7.9047
```

**影响**

一个通过配置校验的确定性请求会随 seed 变成 422/worker failure；调用者无法仅靠合法 N/M/K 预测生成是否成功。这破坏“确定性种子生成器”的可用性，也使大规模 N 配置不可靠。

**必须修复**

保证所有生成边在写出前满足单调高程，例如按相邻 branch/index 的最小高程间距缩放或限制扰动，而不是接受后再整体拒绝；增加 `N` 边界、`M` 较大、不同 random_seed 的参数化测试，并明确允许的最大 N/M/K。

### MEDIUM-1：`invert_min_m`/`invert_max_m` 不是实际生成边界

**证据**

- 配置声明并校验了 invert 上下界：`backend/app/services/hydraulic/seed.py:49-50`、`backend/app/services/hydraulic/seed.py:70-71`。
- 生成公式直接加 `rng.uniform(-0.04, 0.04)`，没有 clamp 或按 span 缩放：`backend/app/services/hydraulic/seed.py:317-324`。
- 测试只覆盖 N/M/K 数值约束，没有覆盖连续参数边界：`backend/tests/hydraulic/test_topology_seed.py:213-225`。

执行：

```bash
# cwd: smart-city-drainage/backend
PYTHONPATH=. python - <<'PY'
from app.services.hydraulic.seed import SeedConfig, generate_tree_loop_seed
cfg = SeedConfig(n=1, m=2, k=0, random_seed=0)
seed = generate_tree_loop_seed(cfg)
print('configured_invert_bounds=', (cfg.invert_min_m, cfg.invert_max_m))
print('generated_inverts=', [(n.stable_key, n.invert_elevation_m) for n in seed.nodes])
print('out_of_bounds=', [(n.stable_key, n.invert_elevation_m) for n in seed.nodes if not cfg.invert_min_m <= n.invert_elevation_m <= cfg.invert_max_m])
PY
```

输出：

```text
configured_invert_bounds= (0.5, 8.0)
generated_inverts= [('SH-N-000-000', 8.0206), ('SH-N-000-001', 6.1057), ('SH-N-OUTFALL', 0.5)]
out_of_bounds= [('SH-N-000-000', 8.0206)]
```

**影响**

配置和 canonical seed 对“边界”的含义不一致；下游若把这些字段用于渲染、质量门禁或数据生成，会得到合同外值。

**必须修复**

扰动幅度必须小于可用 margin 并在生成后显式验证/限制在配置闭区间；增加最小/最大 seed 和不同随机种子的边界测试。

## 测试覆盖评价

- 任务指定的 focused 文件在本会话通过，但它只证明现有 13 个断言通过，不能覆盖上述高风险边界。
- 当前参数化测试未覆盖 `SELF_LOOP`、`MISSING_INVERT`、环路精确成员、disconnected 的 simulation eligibility、大 N 生成或连续配置边界。
- 固定 golden fixture 目录与 expected golden JSON 未在本 ownedPaths 中创建/核验；本审查不把该跨模块缺口计为本 ownedPaths 的实现 finding。

## 未运行/未验证

- 未运行最终全量 pytest、构建、数据库、API、PostGIS、solver、持久化绑定或 live Shanghai device 集成；按任务要求不重复最终全量门禁。
- 未在冻结目标 Python 3.11 运行 focused tests；本会话只有 Python 3.14.4 证据。
- 未验证 API/ORM/durable worker 对 candidate binding 的事务持久化和 operator/admin 显式确认；这些路径不在本模块 ownedPaths。
