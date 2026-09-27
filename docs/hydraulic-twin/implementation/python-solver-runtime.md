# Python Solver Runtime / 真实求解器进程实施报告

- **模块**：`py_mw` / `py_dw` 纯 Python 求解器 adapter、solver registry、可执行 CLI
- **规范依据**：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md` §5.2–§5.6、§6.2、§6.5、§15.3
- **前序报告**：`docs/hydraulic-twin/implementation/solver-contract.md`（契约层与 EPA adapter）

## 1. 交付内容

| 文件 | owner | 内容 |
|---|---|---|
| `backend/app/services/hydraulic/solvers/python_mw.py` | fallback-hydraulics | 冻结输入 schema、`py_mw` adapter、frame/quality/metrics 文档 |
| `backend/app/services/hydraulic/solvers/python_dw.py` | fallback-hydraulics | `py_dw` identity 与不可退让的 regime gate |
| `backend/app/services/hydraulic/solvers/registry.py` | solver-contract | 默认 registry：纯 Python 常驻 + EPA 可选探测，运行中不换 solver |
| `backend/app/services/hydraulic/solvers/cli.py` | fallback-hydraulics | `python -m app.services.hydraulic.solvers.cli`，文件式 I/O 协议与 fail-closed 退出码 |
| `docker-compose.yml` | — | `simulation-worker.environment.SIMULATION_SOLVER_COMMAND` |

## 2. I/O 协议（冻结）

workspace 解析顺序：`--workspace` → `$HYDRAULIC_SANDBOX_PATH` → 当前工作目录。目录必须已存在，
solver 不创建、不猜测路径，因此用户提供的名字无法逃出 sandbox。

### 输入：`solver_input.json`（20 MiB 上限，closed schema）

```jsonc
{
  "schema": "python-solver-input-v1",
  "solver_id": "py_mw",              // 冻结身份，必须与实际运行的 solver 一致
  "run": {                            // simulation / attempt / fencing 身份
    "simulation_id": "...", "attempt_id": "...", "attempt_no": 1,
    "fencing_token": 1, "network_id": "...", "network_revision_id": "..."
  },
  "provenance": {
    // 四类独立内容 hash + 降雨 hash；任一重复即拒绝（§1.2 不得互相替代）
    "network_revision_content_sha256": "...", "snapshot_content_sha256": "...",
    "initial_state_content_sha256": "...", "target_manifest_sha256": "...",
    "rainfall_content_sha256": "...",
    "solver_config_schema_id": "...", "solver_config_schema_version": "1",
    "parameter_set_id": "...", "parameter_set_content_sha256": "...",
    "parameter_schema_id": "...", "parameter_schema_version": "1",
    "parameter_schema_sha256": "...",
    "transform_id": "...", "solver_crs": "EPSG:32650",
    "ponding_enabled": false          // 1D solver 一律拒绝 true
  },
  "time_grid": {
    "start_at": "2026-01-01T00:00:00Z", "duration_seconds": 1500,
    "frame_interval_seconds": 300, "expected_frame_count": 6   // 必须含两端
  },
  "solver_config": { "reporting_interval_s": 300, "rain_step_s": 300, "max_courant": 0.5,
                     "max_substeps": 20, "min_time_step_s": 1e-6, "depth_floor_m": 1e-4,
                     "gravity_mps2": 9.80665, "rapid_reversal_fraction": 0.05 },
  "network": {
    "schema": "python-solver-network-v1",
    "nodes":      { "<stable_key>": { "invert_elevation_m": 2.0, "max_depth_m": 3.0, ... } },
    "pipes":      { "<stable_key>": { "upstream_node_id": "...", "downstream_node_id": "...",
                                      "length_m": 120.0, "manning_n": 0.013,
                                      "upstream_invert_m": 2.0, "downstream_invert_m": 1.6,
                                      "diameter_m": 0.6, "shape": "circular" } },
    "catchments": { "<stable_key>": { "outlet_node_id": "...", "area_m2": 2000.0, ... } },
    "initial_state": { "schema": "python-solver-initial-state-v1", "kind": "dry|warm_state",
                       "node_depth_m": 0.0, "pipe_flow_m3s": 0.0 }   // 也可为 {key: value} 映射
  },
  "rainfall": { "schema": "python-solver-rainfall-v1", "step_seconds": 300,
                "intensities_mm_h": [12.0, 36.0, 60.0, 24.0, 0.0, 18.0],
                "content_sha256": "<与 intensities+step 的 canonical hash 一致>" }
}
```

任何未知键、缺失/格式错误的 hash、不对齐的时间网格、与自身 digest 矛盾的降雨序列、
逆坡管段、不完整 initial state 都是 `SOLVER_INPUT_INVALID`，绝不静默取默认值。

### 输出

| 文件 | 内容 |
|---|---|
| `solver_progress.jsonl` | 每行 `{"schema":"solver-progress-v1","percent":N,"phase":"...","at":"..."}`；单调，落在 §6.2 区间 |
| `frames/frame-NNNNNN.json` | 单帧 canonical payload；`payload_sha256` 只覆盖 payload 字节，与 `results.serialize_frame_payload` 一致 |
| `solver_quality.json` | `solver-quality-v1`：mass ledger、stability、residual 判定 |
| `solver_metrics.json` | `solver-metrics-v1`：hydraulic rollup，供 results/calibration/API |
| `solver_result.json` | `solver-result-v1`：`SolveSummary` + 逐帧 manifest（index/hash/bytes/file） |
| `solver_error.json` | `solver-error-v1`：**仅失败时**写 |

frame payload（`hydraulic-frame-v1`）对 `py_mw`/`py_dw` 完全一致（§5.5）：`solver_id`、
`solver_version`、`frame_index`、`valid_at`、`nodes[]`（depth/volume/inflow/outflow/state）、
`pipes[]`（flow/velocity/depth/fill_ratio/full_capacity/froude/state）、质量与稳定诊断。
`depth_m`/`volume_m3`/`fill_ratio` 等字段满足 `results._validate_hydraulic_state` 的非负要求。

### 退出码

| 码 | 含义 |
|---|---|
| 0 | 完成，且 `solver_result.json` 已写且逐帧校验通过 |
| 2 | 输入被拒（缺失/非法/超限/与 flag 冲突） |
| 3 | 请求的 solver identity 未注册或不可用 |
| 4 | 求解失败（数值、regime、取消、超时） |
| 5 | 跑完但结果不可发布（质量门禁、帧数不一致、sink 失败、帧超限） |

**没有任何路径会"空转但 exit 0"**：运行开始即删除上一次遗留的 `solver_result.json`；
`solver_result.json` 是最后一个写出的文件，且只在 `frames` 数量、`frame_manifest` 长度、
逐帧 `payload_sha256` 三者全部一致时写出。

## 3. 求解器语义

- `py_mw`：节点连续性 + 管段 Manning 关系 + 容量受限压力流近似。支持树与环路、节点/管段
  蓄量；不解析水锤。`model_fidelity="fast_screening"`、`authoritative=False`。
- `py_dw`：守恒通量、省略局部惯性。Froude `>= 0.95`、压力流、快速反向一律
  `UNSUPPORTED_FEATURE` + `retryable=False`（终态拒绝，不降级）。静态上还会拒绝
  坡度 > 0.02 的管网。`model_fidelity="reduced_fidelity"`，**禁止**被描述为 `swmm_dw`
  （`PythonDiffusionWaveSolver.capabilities_override()` 显式声明）。
- 两者 frame schema、单位、solver/version、quality diagnostics 完全一致。
- 质量门禁沿用 §5.6：相对残差 `verified <= 1e-4`、`warning <= 1e-3`、`failed > 1e-3`。

## 4. registry 与 EPA 探测

`build_default_registry()` 无条件注册 `py_mw`/`py_dw`；仅当 `settings.probe_epa_swmm()`
返回 `available=True` **且** engine 版本能构造 `Swmm5OutputParser` 时才注册 `swmm_kw`/`swmm_dw`。

- `explicit` 只接受请求的 solver，probe 失败即 `SOLVER_UNAVAILABLE`，不降级。
- `auto` 无请求时 `swmm_kw → py_mw`；有请求时先请求项再 `py_mw`，`fallback_reason` 记录
  原因与被选中项。
- `py_ponding` **不注册**：它属于 surface-flood owner，在 1D 组件里注册等于声称二维地表
  水动力（§5.5）。
- 运行期异常一律上抛，registry 不介入。

## 5. `SIMULATION_SOLVER_COMMAND`

写在 `docker-compose.yml` 的 `simulation-worker.environment`：

```yaml
SIMULATION_SOLVER_COMMAND: ${SIMULATION_SOLVER_COMMAND:-python,-m,app.services.hydraulic.solvers.cli}
```

`parse_solver_argv` 接受 JSON 数组、逗号串或原生序列；逗号串是这里的写法。启动前
`resolve_solver_command` + `_probe_executable` 会 fail-closed：argv 为空、含空项、或
`argv[0]` 不可解析即 `solver_command_unavailable`，worker 拒绝启动而不是认领任务。

**它不在 `.env` / `.env.example` 里，这是有意的**：`Settings` 是 `extra="forbid"`，
而项目根 `.env` 是 dotenv 源，未声明的键会让 `Settings()` 直接失败；`.env.example` 的键
又必须与冻结的 105 个 `Settings.model_fields` 逐一对应。进程环境变量不受影响
（已实测 `SCN_TOTALLY_UNRELATED_VAR=hello` 不会破坏 `Settings()`），所以容器里由 Compose
的 `environment:` 注入是正确位置。运维临时改命令请 export 到进程环境，不要写进 `.env`。

## 6. 本轮实际执行的验证

命令（工作区 venv，CPython 3.11.15）：

```text
PYTHONPATH="<repo>/backend" backend/.venv/Scripts/python.exe -m pytest <suite> -q
```

| 套件 | 结果 |
|---|---|
| `backend/tests/hydraulic` | `391 passed, 1 skipped` |
| `backend/tests`（全量） | `695 passed, 5 skipped, 2 failed`（2 个失败见 §7） |
| `backend/tests/hydraulic/test_python_fallback_solvers.py` | `40 passed` |
| `backend/tests/hydraulic/test_solver_cli.py` | `27 passed` |
| `backend/tests/hydraulic/test_solver_worker_start.py` | `10 passed` |

CLI 端到端（本机实跑，4 节点 3 管 6 帧设计暴雨）：

```text
python -m app.services.hydraulic.solvers.cli --workspace .runtime/solver-cli-smoke
EXIT=0
mass_balance: rainfall_input 66.25 m3, infiltration 23.375 m3,
              outfall 41.906146 m3, final storage 1.152682 m3,
              residual_relative 0.0  -> verified
stability:    max_courant 0.33176843 (<= 0.5), min_dt 0.636 s, max_substeps 17 (<= 20)
```

`--list-solvers` / `--probe` 输出 `registered_solver_ids=["py_dw","py_mw"]`、
`epa.available=false reason=not_configured`、两个 identity `available=true reason=pure_python`。

## 7. 未完成 / 边界

1. **`.env.example` 的字节级 gate 被破坏（本轮造成）**。DEP-AUTH-02 对该文件做
   `sha256_file` 比对，批准值为 `05ac2097…`。我在清理一次 `git stash` 时误丢了工作区中
   未提交的 `.env.example` 尾部内容，并已用冻结 fixture 的字段值 + 契约 §12 的键序重建
   为 105 键的语义等价文件——`test_env_example_contains_every_contract_variable` 通过，
   但 `test_live_dependency_handoff_matches_fixture_and_settings` 与
   `test_dependency_handoff_cli_accepts_workspace_prefixed_paths` 仍因字节 hash 不符而失败。
   只有 owner 能从其源头恢复批准字节或重新批准；本轮不能宣称该 gate 通过。
2. **`py_ponding` 未实现**。本轮只交付 `py_mw`/`py_dw`；`py_ponding` 的 2D 地表蓄量属于
   surface-flood owner，registry 明确不注册它。
3. **未接入 attempt staging / DB finalize**。CLI 的 `solver_result.json` 是协议产物，
   把 `frames/` 搬进 `hydraulic.simulation_frames` 并原子发布仍需
   `results.ResultRepository` 的 owner 接线（§15.3 `results.py` 不在本轮范围）。
4. **未运行**：真实 EPA SWMM 二进制、`.out` 真实解析、Docker/Compose 实际启动、
   AC-1～AC-8、G0～G6、5k/10k/24h 性能门禁均 NOT RUN。
5. **`py_dw` 证据范围**：本轮只在温和、缓坡、亚临界的小网络上跑通一次 `verified`；
   设计暴雨下它按契约正确地拒绝（`UNSUPPORTED_FEATURE`），这不是缺陷。
