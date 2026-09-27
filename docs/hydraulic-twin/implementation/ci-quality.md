# CI、端到端与性能门禁实施记录

## 0. 证据边界

- 实施日期：2026-09-24。
- 仓库基线：`ca99e0485fb12fbecbd565e79d81f745f8d4a748`；本轮工作树为 dirty，未提交、未推送。
- 本记录只把本轮实际执行的命令作为运行证据。源 PRD 要求 5000 节点/10000 管段 24h 仿真 `<=180s`、前端 25fps，并要求真实 pytest/Vitest/build/Compose，见 `docs/hydraulic-twin/SOURCE_PRD.md:26-32`；冻结实现契约把 G5 四个 harness、参数与 JSON 判据固定在 `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2501-2525`。
- 状态语义：`PASS` 仅表示本轮目标命令 exit 0；`FAIL` 表示命令真实执行但未满足判据；`BLOCKED` 表示固定输入、生产实现或同轮证据缺失；`NOT RUN` 表示没有执行。Compose config 不会被写成 live G6 或 AC-8 PASS，这与 `PRD_SUPPLEMENT.md:579-592` 的证据边界一致。
- 本模块未修改 `docker-compose.yml`、`frontend/package.json`、后端生产实现、PRD 或冻结契约；这些路径的现状只作为门禁输入读取。

## 1. 实际交付

### 1.1 阻断 CI

`.github/workflows/ci.yml` 已改为真实执行并阻断：

- G0 job 运行固定 Python 3.11、Node 18.20.8、npm 10.8.2，执行全量 pytest、Black、isort、Flake8、mypy、backend lifespan、npm ci、Vitest、lint、format、build；mypy 和所有 gate 均无 `continue-on-error`，见 `.github/workflows/ci.yml:77-115`。
- 独立 frontend job 真实执行 `npm ci`、Vitest、ESLint、Prettier 和 production build，并上传 `dist`，见 `.github/workflows/ci.yml:117-146`。
- Compose job 对 `portable` 与 `postgis` 分别运行原始 `docker compose config --quiet`，再运行本模块 verifier；见 `.github/workflows/ci.yml:149-199`。
- 浏览器 E2E job 使用 production build、fixture-backed API/SSE 与本机/Runner Chrome，见 `.github/workflows/ci.yml:202-238`。
- G5 job 调用冻结 verifier，而不是只检查文件存在；见 `.github/workflows/ci.yml:241-275`。
- acceptance job只聚合同一 `RUN_ID`、同一 commit 的 JSON artifact；缺任一 gate 即 `BLOCKED`，见 `.github/workflows/ci.yml:278-332`。
- 所有 job 有 10～45 分钟上限；Python/npm/Node/Alembic/Compose/live 等待都由 verifier 子步骤进一步限时，避免无界 CI。

### 1.2 后端 5000/10000 基准

`scripts/benchmark_hydraulic.py` 是 fail-closed harness，不包含伪造 solver：

- fixture 必须是仓库内 `hydraulic-solver-benchmark-v1`，并验证 network/rainfall/target-manifest/initial-state/solver-config 的逐文件 SHA-256，见 `scripts/benchmark_hydraulic.py:192-286`。
- 固定计数为 5000 nodes、10000 pipes、1000 catchments、289 frames、24h/5min；`py_mw` 之外、180s 之外或 hash/版本不符均阻断，见 `scripts/benchmark_hydraulic.py:43-56`、`scripts/benchmark_hydraulic.py:227-286`。
- 生产 executor 必须把 frame 流式写入 harness sink；sink 校验连续 frame index、5 分钟时间网格、attempt_id、289 帧、frame bytes/hash，并要求 executor 调用 `finalize`，见 `scripts/benchmark_hydraulic.py:401-491`。
- 固定 1 次预热 + 3 次正式运行、4 CPU、8 GiB、4 BLAS/OMP 线程、无 GPU；queue wait 单列，worker wall time `<=180s`，质量 residual `<=1e-4`，持久化/发布证据必须为 true，见 `scripts/benchmark_hydraulic.py:693-771`、`scripts/benchmark_hydraulic.py:865-1007`。
- 输出 JSON 与 `.sha256` sidecar；退出码 `0=PASS`、`1=FAIL`、`2=BLOCKED`、`3=INTERNAL`，见 `scripts/benchmark_hydraulic.py:1018-1143`。
- `--self-test` 覆盖合法 manifest、hash 漂移拒绝与 P95 计算，本轮 PASS。

当前固定 solver fixture 不存在，因此真实 solver benchmark **未执行**，脚本按设计返回 `BLOCKED/2`；没有用参考循环或文档勾选替代。

### 1.3 前端 10k 地图性能 smoke

`frontend/performance/run-twin-map.mjs` 与固定 fixture 提供可重复的算法 smoke：

- fixture 固定 5000 nodes、10000 pipes、1000 catchments、289 frames，见 `frontend/performance/fixtures/twin-map-5k-10k-v1.json:6-25`。
- 实现 64×64 spatial grid、viewport culling、289-frame 有界 FrameCache、4x playback、layer toggle 与 selection 切换，见 `frontend/performance/run-twin-map.mjs:228-338`、`frontend/performance/run-twin-map.mjs:340-425`。
- 输出 sample count、P95/max update、feature operations/s、cache bytes、visible ratio 与 SHA-256；退出码和 JSON sidecar 见 `frontend/performance/run-twin-map.mjs:427-555`。
- 该脚本明确标记 `NON_ACCEPTANCE_SMOKE`，不声称 browser presented FPS、RAF P95、browser long task、Canvas、API P95 或应用地图集成；见 `frontend/performance/run-twin-map.mjs:4-10`、`frontend/performance/run-twin-map.mjs:488-506`。完整浏览器 25fps 仍由冻结的 `test:perf:twin`/真实应用实现负责，不能由本 smoke 替代。

### 1.4 AC 浏览器 E2E

`frontend/e2e/run-ac-smoke.mjs`：

- 启动本地 production `dist` server 和 hash-locked API fixture server，支持 JSON 与 SSE，见 `frontend/e2e/run-ac-smoke.mjs:464-544`。
- 通过 Chrome DevTools Protocol 启动/关闭 headless Chrome，保留 browser version、请求、异常、console error、fixture/build hash，见 `frontend/e2e/run-ac-smoke.mjs:355-462`、`frontend/e2e/run-ac-smoke.mjs:760-869`。
- 当前固定场景覆盖 AC-3 replay、AC-4 dashboard forecast、AC-4 alert/SSE、AC-5 calibration、AC-6 A/B、AC-7 legacy login，见 `frontend/e2e/fixtures/ac-smoke-v1.json:15-180`。
- 任一 selector、API 请求、浏览器异常或场景失败均返回 `FAIL/1`；fixture/build/browser 缺失返回 `BLOCKED/2`。

### 1.5 Compose/全门禁 verifier

`scripts/verify_hydraulic_stack.py`：

- G0 真实执行全量测试/lint/type/lifespan/frontend，见 `scripts/verify_hydraulic_stack.py:459-663`。
- G1～G4 要求冻结 fixture 及其 `gate_commands`；只有文件存在但没有可执行命令时仍为 `BLOCKED`，见 `scripts/verify_hydraulic_stack.py:40-62`、`scripts/verify_hydraulic_stack.py:664-724`。
- G5 调用 backend 5k/10k、frontend 60s smoke，并要求 API/nowcast harness 与冻结 npm entrypoint；任一缺失即 `BLOCKED`，见 `scripts/verify_hydraulic_stack.py:726-876`。
- G6 对两个 profile 分别运行真实 `docker compose config`/JSON render，检查服务拓扑、migration dependency、digest、端口、secret、init SQL 与 SSE nginx 契约，见 `scripts/verify_hydraulic_stack.py:878-1039`、`scripts/verify_hydraulic_stack.py:1041-1158`。
- G6 `config` 模式明确不是 live G6/AC-8；live 模式才允许在静态/fixture 全绿后执行 fresh-volume up、readiness probes，见 `scripts/verify_hydraulic_stack.py:1109-1158`。
- acceptance 严格校验同 run/commit、非 PASS、缺 artifact，见 `scripts/verify_hydraulic_stack.py:1271-1397`。

## 2. 本轮真实命令与结果

### 2.1 质量与回归

| 命令 | 结果 |
|---|---|
| `uv venv smart-city-drainage/.tmp-ci-quality/venv311 --python 3.11 && uv pip install --python smart-city-drainage/.tmp-ci-quality/venv311/Scripts/python.exe --require-hashes -r smart-city-drainage/backend/requirements-py311.lock` | exit 0；Python `3.11.15`；`pip check` 输出 `No broken requirements found.` |
| `PYTHONPATH=smart-city-drainage/backend DATABASE_URL=sqlite+aiosqlite:///./test.db REDIS_URL=redis://127.0.0.1:1/0 INFLUXDB_URL=http://127.0.0.1:1 JWT_SECRET_KEY=ci-quality-test-secret smart-city-drainage/.tmp-ci-quality/venv311/Scripts/python.exe -m pytest smart-city-drainage/backend/tests -q --tb=short` | **FAIL/1**：`5 failed, 212 passed, 4 skipped, 1 warning in 14.19s`。失败为 2 个 migration gate、2 个 dependency handoff hash drift、1 个仍断言 performance 文件必须不存在的旧测试；最后一个断言见 `backend/tests/test_contract_gate_scripts.py:318-327`。 |
| `npm --prefix smart-city-drainage/frontend test` | **FAIL/1**：`3 failed, 78 passed`；Vitest 汇总 `3 failed / 14 passed test files`。失败为 LoginPage localStorage、fixture 自引用、simulation task state。 |
| `npm --prefix smart-city-drainage/frontend run lint` | **FAIL/1**：owned Node harness 已无 error；全仓仍有 2 个 out-of-scope warning，`--max-warnings 0` 生效。 |
| `npm --prefix smart-city-drainage/frontend run format:check` | **FAIL/1**：45 个既有 `src/**` 文件不符合 Prettier。 |
| `npm --prefix smart-city-drainage/frontend run build` | **PASS/0**：`4042 modules transformed`，`built in 13.80s`；仍有 ECharts chunk 大于 1000 kB 警告。 |
| `.../python -m black --check scripts/benchmark_hydraulic.py scripts/verify_hydraulic_stack.py` + isort + project Flake8 | **PASS/0**。 |
| `eslint ... frontend/e2e/run-ac-smoke.mjs frontend/performance/run-twin-map.mjs --max-warnings 0` + owned Prettier | **PASS/0**。 |
| `python -m py_compile ...` + 两个 `node --check` | **PASS/0**。 |
| PyYAML 解析 `.github/workflows/ci.yml` | **PASS/0**：8 个 jobs：`dependency-lock, host-interpreter, g0-quality, frontend-quality, compose-config, frontend-e2e, g5-performance, acceptance-summary`；`rg continue-on-error` 无命中。 |

### 2.2 Compose

以下两个原始命令均 **PASS/0、无输出**：

```bash
HYDRAULIC_SPATIAL_BACKEND=portable ... docker compose --profile production --profile monitoring -f smart-city-drainage/docker-compose.yml config --quiet
HYDRAULIC_SPATIAL_BACKEND=postgis ... docker compose --profile production --profile monitoring -f smart-city-drainage/docker-compose.yml config --quiet
```

最终 verifier 命令：

```bash
smart-city-drainage/.tmp-ci-quality/venv311/Scripts/python.exe smart-city-drainage/scripts/verify_hydraulic_stack.py --gate g6 --profile portable --mode config --run-id local-ci-quality --evidence-dir artifacts/hydraulic/local-ci-quality/g6/portable
smart-city-drainage/.tmp-ci-quality/venv311/Scripts/python.exe smart-city-drainage/scripts/verify_hydraulic_stack.py --gate g6 --profile postgis --mode config --run-id local-ci-quality --evidence-dir artifacts/hydraulic/local-ci-quality/g6/postgis
```

两者均 **FAIL/1**，而内部 `docker compose config --quiet`/JSON render 两步均 **PASS/0**。阻断项包括：

- 缺 `network-job-worker`、`simulation-worker`、`nowcast-worker`、`outbox-relay`、`notification-worker`；
- 第三方镜像未 digest pin，Postgres/Redis/Influx 默认暴露宿主端口；
- Compose 中存在硬编码 secret/default；
- postgis profile 未选择明确 PostGIS 镜像；
- 缺 `docker/hydraulic-gates/compose-v1.lock.json` 与 restore manifest。

因此本轮只证明 Compose 可渲染，不证明 G6/AC-8 live PASS。

### 2.3 后端基准

自测：

```bash
smart-city-drainage/.tmp-ci-quality/venv311/Scripts/python.exe smart-city-drainage/scripts/benchmark_hydraulic.py --fixture unused --output unused --self-test
```

**PASS/0**：`valid_manifest/hash_validation/hash_drift_rejected/p95_nearest_rank` 全部 PASS。

冻结规模命令：

```bash
smart-city-drainage/.tmp-ci-quality/venv311/Scripts/python.exe smart-city-drainage/scripts/benchmark_hydraulic.py --fixture backend/tests/fixtures/performance/solver/5k_10k_24h_v1/manifest.json --warmup 1 --repeats 3 --cpus 4 --memory 8g --threads 4 --output smart-city-drainage/.tmp-ci-quality/backend-benchmark-final.json --run-id local-ci-quality
```

**BLOCKED/2**：`benchmark_executed=false`，错误为固定 manifest 缺失；artifact SHA-256 `d1465e4923e372136fd23e05e2b7f9729a77af2b4cc8eb08ac42593db89d63cd`。真实 5000/10000 solver wall time、P95、RSS、质量本轮 **NOT RUN**。

### 2.4 前端 10k smoke

```bash
node smart-city-drainage/frontend/performance/run-twin-map.mjs --fixture smart-city-drainage/frontend/performance/fixtures/twin-map-5k-10k-v1.json --duration-seconds 60 --output smart-city-drainage/.tmp-ci-quality/frontend-smoke-final.json --run-id local-ci-quality
```

**PASS/0**，但仅为 `NON_ACCEPTANCE_SMOKE`：

- 2880 samples；P95 update `29.7552ms`；max `43.4754ms`；
- `65781.23` feature operations/simulated-second；
- 5000/10000/1000 fixture；289-frame cache `18,496,000` bytes，低于 `256 MiB`；
- actual execution `32.11s`；artifact SHA-256 `9be3726326654019d1431fbdfe5588301496cf3ada82feb6662d9c08eacd3c3e`；
- presented FPS、RAF、browser long task、Canvas 与应用集成仍 **NOT RUN/NOT EVALUATED**。

### 2.5 AC 浏览器 E2E

本轮使用真实 Chrome `153.0.8010.53`、CI 相同的 `120s` 总 timeout/`20s` 每场景 timeout：

```bash
CHROME_PATH='C:/Program Files/Google/Chrome/Application/chrome.exe' timeout 180s node smart-city-drainage/frontend/e2e/run-ac-smoke.mjs --fixture frontend/e2e/fixtures/ac-smoke-v1.json --output artifacts/hydraulic/local-ci-quality/e2e/ac-smoke.json --timeout-seconds 120 --scenario-timeout-seconds 20 --run-id local-ci-quality
```

**FAIL/1**，`browser_executed=true`，artifact SHA-256 `c1d513b695a90fc06dc7d3327f28e582249ea271b4aba3bbb2394adba3299f0d`。真实结果：

- AC-3：`/simulation` 缺少 `simulation-page`，最终重定向 `/`，未请求 simulation detail/frame manifest；
- AC-4 dashboard：缺 forecast/risk selector 与 API；
- AC-4 alerts：请求了 alerts/SSE，但缺 forecast alert selector；
- AC-5/AC-6：`/simulation?...` 重定向 `/`，未请求 calibration/comparison；
- AC-7 legacy login：当前 production build 未出现 `form` selector。

这正是新增阻断门禁期望捕获的现状；没有把 E2E FAIL 改写为 PASS。

### 2.6 G5 与 acceptance 聚合

```bash
smart-city-drainage/.tmp-ci-quality/venv311/Scripts/python.exe smart-city-drainage/scripts/verify_hydraulic_stack.py --gate g5 --run-id local-ci-quality --evidence-dir smart-city-drainage/.tmp-ci-quality/g5-final2
```

**BLOCKED/2**：frontend 60s smoke step **PASS/0**；整体因缺 solver/API/nowcast fixtures、`benchmark_hydraulic_api.py`、`benchmark_hydraulic_nowcast.py` 和冻结 `test:perf:twin` entrypoint 而阻断。G5 artifact SHA-256 `4bc22e1fe6bae60ed594fc27f9ea63e20bcbeb15ed5c1bfcde60d833f5ad2b49`。

```bash
smart-city-drainage/.tmp-ci-quality/venv311/Scripts/python.exe smart-city-drainage/scripts/verify_hydraulic_stack.py --gate acceptance --ac ac1,ac2,ac3,ac4,ac5,ac6,ac7,ac8 --run-id local-ci-quality --evidence-dir artifacts/hydraulic/local-ci-quality/acceptance
```

**BLOCKED/2**：AC-1～AC-8 均未形成同轮 PASS artifact；artifact SHA-256 `b7204ec16cb35c14fb348223216648dc7b6ff293c85a4fb696c4c3fd0a469292`。

## 3. 风险与明确未完成项

1. **真实 5k/10k solver 性能未取得。** 固定 manifest 与生产 executor 不在 ownedPaths；当前只能证明 harness fail-closed、自测和机器输出正确。
2. **完整浏览器 10k 性能未取得。** 当前 smoke 是算法/缓存负载；真实 Leaflet/应用页面、presented FPS、RAF P95、long task、Canvas 与 API latency 仍需 production map 和冻结 npm entrypoint。
3. **E2E 当前为真实 FAIL。** `/simulation`、forecast/calibration/comparison UI 尚未落地；CI 应保持红。
4. **G6/AC-8 live 未运行。** 只运行了 config；当前 Compose 静态契约和 frozen restore fixture 本身不绿，且本轮未获授权修改 Compose/数据库/worker 文件。
5. **G0 当前不绿。** 全量 pytest/Vitest/lint/format/mypy/lifespan 失败由当前工作树真实产生；新增 job 会阻断合并，未使用 `continue-on-error`。
6. **并行工作树冲突明确存在。** 例如 `backend/tests/test_contract_gate_scripts.py:318-327` 仍要求本任务新增的 performance 文件不存在；`frontend/package.json` 的冻结 `test:perf:twin` 也不在本模块 ownedPaths。上述路径未被本模块覆盖。
7. **远端 GitHub Actions 未运行。** 本轮未 push、未创建 PR、未触发远端 runner；只验证了 workflow YAML 与本机对应命令。

## 4. 文件清单

- `.github/workflows/ci.yml`
- `scripts/benchmark_hydraulic.py`
- `scripts/verify_hydraulic_stack.py`
- `frontend/e2e/run-ac-smoke.mjs`
- `frontend/e2e/fixtures/ac-smoke-v1.json`
- `frontend/e2e/fixtures/api-v1/manifest.json`
- `frontend/e2e/fixtures/api-v1/simulation.json`
- `frontend/e2e/fixtures/api-v1/forecast.json`
- `frontend/e2e/fixtures/api-v1/calibration.json`
- `frontend/e2e/fixtures/api-v1/comparison.json`
- `frontend/performance/run-twin-map.mjs`
- `frontend/performance/fixtures/twin-map-5k-10k-v1.json`
- `docs/hydraulic-twin/implementation/ci-quality.md`
