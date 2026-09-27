# CI、端到端与性能门禁独立审查

- 模块：`ci-quality`
- 审查日期：2026-09-25
- 审查方式：只读复核冻结契约、真实 workflow/脚本/fixture，并运行小型定向诊断；未修改实现代码。
- 任务规格：更新 CI 以运行真实 pytest/Vitest/build/Compose config；增加 AC E2E、10k 地图性能脚本、后端 5000/10000 基准；脚本应有明确退出码、机器可读输出与合理超时；禁止用文档勾选替代证据。
- 结论：**NOT APPROVED / `approved=false`**。发现 **8 个 High、1 个 Medium**；任一 High 均足以阻断批准。

## 1. 判定摘要

| ID | 严重度 | 结论 |
|---|---|---|
| CQ-01 | High | acceptance 可接受伪造 JSON，并可把 `mode=config` 的 G6 artifact 当作 AC-8 证据 |
| CQ-02 | High | G1～G4 只执行 fixture 自报的任意 `gate_commands`；`python -c pass` 可得到全 PASS |
| CQ-03 | High | G5 没有执行冻结的四个 harness；API/nowcast 只检查文件存在，前端只跑非验收 smoke |
| CQ-04 | High | 后端 benchmark 在配置线程环境前先校验环境，冻结 CI 命令会提前 `BLOCKED` |
| CQ-05 | High | benchmark 未强制 4 个物理核，只选择 4 个 affinity CPU，性能证据可在错误拓扑上产生 |
| CQ-06 | High | AC E2E fixture 与真实/冻结 API 契约不兼容，且 runner 会把 404 请求计为“已请求” |
| CQ-07 | High | `frontend-e2e` 未依赖 `frontend-quality`，并行下载尚未生成的 build artifact，存在确定性竞态 |
| CQ-08 | High | CI 没有运行/上传 G1～G4，acceptance-summary 永远无法形成其声明的 AC-1～AC-8 全量 PASS |
| CQ-09 | Medium | 前端 smoke 的时长与阈值由参数/fixture 决定，工作负载并未按 60 秒运行，不能作为性能趋势门禁 |

## 2. 详细发现

### CQ-01（High）：acceptance 证据完整性不足，config-only G6 可被当成 AC-8 PASS

**问题**

`_acceptance_artifact()` 只核对 `run_id`、`commit`、`status=PASS`、`exit_code=0`；不核对 JSON schema、gate 名、profile、`mode`、必需证据字段、sidecar SHA-256，也不确认 G6 是 live。AC-8 又直接依赖 G6 artifact。因此一个手写的最小 JSON，甚至 `mode=config` 的 G6 JSON，也能满足 artifact 有效性判断。

**证据**

- 冻结契约要求 G6 两 profile 都从 fresh volume 完成 live 链路，Compose config 不能替代：`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2496-2497`。
- 当前校验仅做四个字段比较：`smart-city-drainage/scripts/verify_hydraulic_stack.py:1249-1267`。
- CI 上传的 Compose artifact 明确使用 `--mode config`：`smart-city-drainage/.github/workflows/ci.yml:169-191`。
- AC-8 映射到 G6：`smart-city-drainage/scripts/verify_hydraulic_stack.py:1307-1315`。
- 定向探针输出：`mode=config accepted= True error= None`。

**影响**

- config、静态文件或人工构造的 JSON 可伪造 AC-8。
- artifact 被修改后也不会校验 `.sha256`，违背“机器证据 + hash”冻结边界。
- 报告中的 `scope_note` 不能阻止下游 acceptance 误用。

**修复建议**

1. 校验 `schema`、`gate`、profile、mode、required steps/evidence、status/exit code。
2. G6 必须分别验证 `mode=live`，两个 profile 都存在且为同 RUN_ID/commit。
3. 校验 sidecar SHA-256，并让 acceptance 只消费已验证 hash 的不可变 artifact。
4. 为每个 AC 校验其专属证据字段，而非只依赖 gate 名称。

### CQ-02（High）：G1～G4 可由任意 no-op 命令伪造 PASS

**问题**

`fixture_step()` 只检查文件存在并计算“当前文件 hash”；`optional_manifest_commands()` 接受 fixture 自报的任意 argv；`gate_domain()` 执行后只看退出码。命令无需消费所有 fixture，无需产生 gate artifact，也无需匹配冻结测试集合。临时目录诊断中，只提供全部 G2 文件并把第一条 manifest 写成 `gate_commands: [["python","-c","pass"]]`，结果 `g2.fixtures` 与 `g2.harness-1` 均为 PASS。

**证据**

- 当前文件存在性/hash 逻辑：`smart-city-drainage/scripts/verify_hydraulic_stack.py:369-412`。
- 任意 argv 解析：`smart-city-drainage/scripts/verify_hydraulic_stack.py:415-442`。
- 直接执行后按退出码聚合：`smart-city-drainage/scripts/verify_hydraulic_stack.py:663-722`。
- 冻结契约要求 G1～G4 的具体迁移、安全、worker、realtime 证据：`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2491-2494`。
- 定向探针关键输出：`g2.fixtures status=PASS`；`g2.harness-1 command=["...python.EXE","-c","pass"] status=PASS`。

**影响**

G1～G4 可在未运行任何真实测试的情况下生成 `result.json` 并被 acceptance 消费，直接违反“禁止文档/文件存在代替证据”。

**修复建议**

- 在 verifier 内固定 gate→允许的测试入口/参数映射，或对 `gate_commands` 做严格 allowlist。
- 每个命令必须声明并实际消费 registry 中全部 fixture。
- 校验 fixture schema、冻结 hash、输出 artifact schema/hash、测试计数和 AC 专属断言。
- 加入针对 no-op、错误 fixture、未生成 artifact、跨 fixture 命令的负向测试。

### CQ-03（High）：G5 没有执行冻结的四个性能 harness

**问题**

`gate_g5()` 实际只执行 solver benchmark 和 `run-twin-map.mjs` 算法 smoke；`benchmark_hydraulic_api.py`、`benchmark_hydraulic_nowcast.py` 仅按“文件存在”记 PASS，`package.json.scripts.test:perf:twin` 也只做字符串比较。未按冻结命令执行 API、nowcast、npm/browser harness，也未校验四个 child JSON 的 schema、阈值、hash 或 RUN_ID。临时目录中用只 `exit(0)` 的假 solver/frontend 脚本，并创建未执行的 API/nowcast 文件后，全部六个 G5 step 均为 PASS，其中 API/nowcast 的 `command=[]`。

**证据**

- 冻结契约要求 G5 原样执行四个固定命令：`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2495`、`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2501-2525`、`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2536`。
- 当前 API/nowcast 仅 presence check：`smart-city-drainage/scripts/verify_hydraulic_stack.py:792-816`。
- npm entrypoint 仅字符串检查：`smart-city-drainage/scripts/verify_hydraulic_stack.py:817-845`。
- 前端实际执行的是非验收 smoke：`smart-city-drainage/scripts/verify_hydraulic_stack.py:772-790`。
- smoke 自身明确不评估 presented FPS、RAF、long task、Canvas、API P95：`smart-city-drainage/frontend/performance/run-twin-map.mjs:4-10`、`smart-city-drainage/frontend/performance/run-twin-map.mjs:500-511`。
- child 命令未传统一 RUN_ID；solver 在缺省时生成自己的时间戳 run ID：`smart-city-drainage/scripts/benchmark_hydraulic.py:864-870`；前端同样自行生成：`smart-city-drainage/frontend/performance/run-twin-map.mjs:473-480`。
- 定向探针输出：API/nowcast step 均为 `status=PASS, command=[]`；其余假脚本也为 PASS。

**影响**

即使文件齐全，G5 仍可在 API/nowcast/真实浏览器性能完全未运行时 PASS；同轮证据也无法绑定到外层 CI `RUN_ID`。

**修复建议**

1. 原样执行冻结的 solver/API/nowcast/npm 四命令并传播退出码。
2. 给所有 child 显式传同一 RUN_ID，或从冻结 output path 派生并校验。
3. 读取四个 child artifact，校验 schema、commit、fixture/hash、阈值与 sidecar。
4. 非验收算法 smoke 可保留为附加信号，但不得替代 G5 browser harness；缺失时 G5 必须 BLOCKED/FAIL。

### CQ-04（High）：后端 benchmark 的冻结命令会在线程配置前提前 BLOCKED

**问题**

`validate_manifest(check_runtime=True)` 先要求 `OMP_NUM_THREADS`、`OPENBLAS_NUM_THREADS`、`MKL_NUM_THREADS`、`NUMEXPR_NUM_THREADS` 已等于 `4`，并要求 `CUDA_VISIBLE_DEVICES` 已为空；`configure_resources()` 却在该验证完成约 500 行之后才设置这些变量。冻结 G5 命令只传 `--threads 4`，没有预设环境，因此在真实 fixture 存在后仍会先返回 BLOCKED，根本到不了 executor。

**证据**

- 提前环境校验：`smart-city-drainage/scripts/benchmark_hydraulic.py:321-353`。
- 调用顺序为 `validate_manifest()` 在 `configure_resources()` 之前：`smart-city-drainage/scripts/benchmark_hydraulic.py:879-908`。
- G5 实际命令没有环境变量：`smart-city-drainage/scripts/verify_hydraulic_stack.py:750-767`。
- 定向探针（模拟 Python 3.11、合法临时 manifest、清除相关环境）输出：`unconfigured_frozen_command_first_error=OMP_NUM_THREADS must be exactly 4 before benchmark start`。

**影响**

当前“fixture 缺失所以 BLOCKED”掩盖了第二个确定性 BLOCKED；即使补齐 fixture，CI 仍无法运行 5000/10000 benchmark。

**修复建议**

- 在任何 runtime validation 前设置并验证资源环境，或让 validation 接收 CLI 配置并验证“即将应用”的配置。
- G5 entrypoint 显式导出四个线程变量与空 `CUDA_VISIBLE_DEVICES`，然后再调用 benchmark。
- 增加使用真实 CLI 顺序的 self-test：合法 manifest + 默认 CI 环境应进入 executor 解析，而不是被预检拦截。

### CQ-05（High）：benchmark 未强制“4 个物理核”资源契约

**问题**

脚本会读取 physical core 数用于 metadata，但资源门禁只要求可用 CPU/affinity 数量不少于 4；随后取 affinity 列表前四项。2 物理核/4 线程的机器也会被接受。通用 GitHub hosted runner 并未绑定物理核拓扑，因此 180s 证据可能在错误硬件上产生。

**证据**

- 规范要求 4 physical cores/8 GiB/无 GPU：`smart-city-drainage/docs/hydraulic-twin/PRD_SUPPLEMENT.md:408-412`、`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2509`。
- 当前只检查可用 CPU 数 `>=4`：`smart-city-drainage/scripts/benchmark_hydraulic.py:328-337`。
- affinity 选择逻辑只是取前四个 CPU：`smart-city-drainage/scripts/benchmark_hydraulic.py:705-715`。
- physical core 解析只写 metadata，未参与门禁：`smart-city-drainage/scripts/benchmark_hydraulic.py:760-783`、`smart-city-drainage/scripts/benchmark_hydraulic.py:798-814`。

**影响**

同一 `PASS` 可能来自 4 线程而非 4 物理核，性能结果不可与其他门禁环境比较。

**修复建议**

- 使用可声明物理核拓扑的专用 runner/VM，或解析 physical/core id 并明确验证 4 个物理核映射。
- 资源报告记录 CPU affinity 与 physical-core ID 集合；不满足时 BLOCKED。
- 将环境拓扑加入 artifact hash/acceptance 校验，而不是仅作说明字段。

### CQ-06（High）：AC E2E fixture 与真实/冻结 API 契约不兼容，runner 也不能证明 AC-3～AC-7

**问题**

E2E fixture 并非由真实 API schema 生成：frame manifest 使用不存在的 `/frames/manifest`，响应也没有 client 要求的顶层 `frames[]`；A/B 使用不存在的 `/api/simulations/comparison-ac-6`；AC-4 SSE 仍走 legacy `/api/sse/events`，且 SSE data 缺少真实 parser 必需的 `schema_version/event_id/sequence/event_type/alert`。此外 fixture server 不校验 Authorization；请求在查 route 之前就记入 `onRequest`，所以 404 也可满足 `expected_requests`。场景仅检查 selector/静态文本，不执行 submit/cancel、播放/拖拽、帧切换、率定重算或 A/B 重算。

**证据**

- 冻结 frame/calibration/A/B/SSE endpoint：`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1626-1635`、`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1677-1699`、`smart-city-drainage/docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1816-1823`。
- 当前真实 client 使用 `/frames` 与 `/simulations/{baseline}/compare/{candidate}`：`smart-city-drainage/frontend/src/features/simulation/api/client.ts:294-315`、`smart-city-drainage/frontend/src/features/simulation/api/client.ts:372-395`。
- frame normalizer 要求顶层 `frames[]`：`smart-city-drainage/frontend/src/features/simulation/model/normalizers.ts:334-368`。
- fixture manifest 使用错误路径并复用 simulation detail body：`smart-city-drainage/frontend/e2e/fixtures/api-v1/manifest.json:4-23`、`smart-city-drainage/frontend/e2e/fixtures/api-v1/manifest.json:46-76`。
- 真实 forecast stream 使用 `/api/forecast/events?network_id=...` 和 Bearer：`smart-city-drainage/frontend/src/features/simulation/forecast/stream.ts:78-110`；其 parser 要求完整 envelope：`smart-city-drainage/frontend/src/features/simulation/forecast/stream.ts:19-52`。
- E2E server 在 route 404 判断前调用 `onRequest`：`smart-city-drainage/frontend/e2e/run-ac-smoke.mjs:463-473`；场景只比较 method/path 集合：`smart-city-drainage/frontend/e2e/run-ac-smoke.mjs:673-708`。
- 当前断言类型只有 selector/text/path/count：`smart-city-drainage/frontend/e2e/run-ac-smoke.mjs:642-670`；fixture 场景没有真实动作与数值重算：`smart-city-drainage/frontend/e2e/fixtures/ac-smoke-v1.json:12-176`。
- acceptance 将这一 artifact 直接用于 AC-3～AC-7：`smart-city-drainage/scripts/verify_hydraulic_stack.py:1307-1315`。

**影响**

- 当前 UI 即使完全符合真实 client，也会因 fixture endpoint/schema 不一致而无法转绿。
- 反过来，静态 selector 与一个 404 请求也可能满足当前场景，无法证明 API/SSE/RBAC/AC 行为。
- acceptance 可能把“smoke 通过”错误提升为 AC PASS。

**修复建议**

- 用冻结 OpenAPI/TypeScript/Pydantic 联合 fixture 生成 API body，修正 `/frames`、A/B、forecast events 和 SSE envelope。
- fixture server 校验 path、query、Authorization、status/content-type；`expected_requests` 只统计成功且满足 header/response 契约的请求。
- 增加 viewer/operator/admin、submit/cancel、pause/seek/1x/2x/4x、frame 0/288、RMSE/NSE 重算、A/B 逐项重算与 incompatible 409 等场景。
- 在这些覆盖完成前，将 artifact 标为 partial smoke，禁止 acceptance 以其判 AC PASS。

### CQ-07（High）：frontend E2E 与 build job 存在 artifact 竞态

**问题**

`frontend-quality` 构建并上传 `frontend-dist`，但 `frontend-e2e` 没有 `needs: frontend-quality`。两个 job 会并发启动；E2E 在 `npm ci` 后立刻下载同轮 artifact，而 artifact 只有在 build job 完成上传后才可下载。build 较慢时，E2E 会在应用启动前失败；build 失败时也无法得到预期 blocker，而变成缺失 artifact 的竞态失败。

**证据**

- build/upload：`smart-city-drainage/.github/workflows/ci.yml:116-146`。
- E2E 下载并运行：`smart-city-drainage/.github/workflows/ci.yml:201-230`；该 job 没有任何 `needs`。
- PyYAML 定向解析输出：`frontend_e2e_needs: null`，且步骤顺序包含 `actions/download-artifact@v4` 后运行 E2E。

**影响**

即使前端实现和 E2E 正确，CI 仍可能随机红；失败证据也无法区分 build 未完成与 AC 回归。

**修复建议**

- 给 `frontend-e2e` 增加 `needs: frontend-quality`；只有 build 成功后启动。
- acceptance 保持 `if: always()`，但对 skipped/missing E2E 明确生成 BLOCKED artifact，而不是让下载步骤直接中断。
- 更稳妥的方案是同一 job build + E2E，或用显式 artifact name 加依赖。

### CQ-08（High）：acceptance-summary 缺少 G1～G4 producer，结构上无法全量 PASS

**问题**

`gate_acceptance()` 要求 G0、G1、G2、G3、G4、G5、G6 和 E2E artifact；CI 却只运行/下载 G0、G6、E2E、G5。workflow 中不存在 G1～G4 job，acceptance 下载步骤也没有对应 artifact。因此 AC-1～AC-6 至少部分永远 BLOCKED；这不是当前实现未绿，而是 workflow 缺少完成路径。

**证据**

- acceptance 必需路径：`smart-city-drainage/scripts/verify_hydraulic_stack.py:1270-1305`。
- workflow 仅下载 G0/G6/E2E/G5：`smart-city-drainage/.github/workflows/ci.yml:277-319`。
- 定向 PyYAML 输出 jobs 为：`dependency-lock, host-interpreter, g0-quality, frontend-quality, compose-config, frontend-e2e, g5-performance, acceptance-summary`；无 G1～G4。

**影响**

模块宣称的“Same-run Acceptance Summary”不能实现 AC-1～AC-8 的同轮汇总；修复其他模块后仍需再次改 workflow 才能验收。

**修复建议**

- 增加 G1～G4 的阻断 jobs，分别上传同 RUN_ID artifact；或从已存在的同 workflow artifact producer 下载。
- 每个 job 显式传同一 RUN_ID，acceptance 校验两 profile 与所有 child hash。
- 在 producer 尚未接入时，移除“AC-1～AC-8 aggregation”措辞，明确该 job 仅是 partial summary。

### CQ-09（Medium）：前端 smoke 的“60 秒”和阈值不是可信性能门禁

**问题**

`--duration-seconds` 只作为 `featureOperations / durationSeconds` 的除数；实际 workload 固定同步执行 2880 步，不按 60 秒节拍/持续时间运行。P95/max/cache/operations 阈值全部从 fixture 读取，可随 fixture 一起放宽。脚本虽正确标注 `NON_ACCEPTANCE_SMOKE`，但若将它的 PASS 当作性能趋势或 G5 子门禁，会产生误导。

**证据**

- CLI duration 只被保存：`smart-city-drainage/frontend/performance/run-twin-map.mjs:93-124`、`smart-city-drainage/frontend/performance/run-twin-map.mjs:473-530`。
- workload 固定由 fixture step 数同步循环：`smart-city-drainage/frontend/performance/run-twin-map.mjs:339-408`。
- 阈值由 fixture 提供并直接执行：`smart-city-drainage/frontend/performance/run-twin-map.mjs:195-213`、`smart-city-drainage/frontend/performance/run-twin-map.mjs:426-457`。
- 当前 fixture 阈值：`smart-city-drainage/frontend/performance/fixtures/twin-map-5k-10k-v1.json:19-36`。

**影响**

该结果可用于发现算法回归/缓存上限，但不能证明 60 秒持续负载、presented FPS 或真实浏览器 frame-time。

**修复建议**

- 保留 smoke 时，把阈值固定在代码或受控 policy 中，并将其定位为回归趋势信号。
- 真实 G5 另用 production map + pinned Chromium 连续运行 60 秒，按冻结指标判定。
- 输出中明确区分 `requested_duration_seconds`、`actual_execution_seconds` 与 browser sampling duration。

## 3. 本轮实际执行的检查

> 依照任务要求，没有重复最终全量门禁。以下是本次审查亲自执行的定向检查；历史 implementation 文档中的结果不当作本轮通过证据。

| 检查 | 实际命令（仓库根=`C:\Users\zyq\Documents\opencode-pn\goal测试`） | 本轮结果 |
|---|---|---|
| 本机运行时 | `python --version; node --version; npm --version` | Python 3.14.4；Node v25.9.0；npm 11.12.1。不是冻结 Python 3.11/Node 18.20.8 |
| Python/Node 语法 | `python -c "...ast.parse(...benchmark_hydraulic.py, verify_hydraulic_stack.py)..." && node --check ...run-ac-smoke.mjs && node --check ...run-twin-map.mjs` | `python_ast_parse=PASS`；`node_check=PASS` |
| E2E fixture/hash 结构 | `node .../run-ac-smoke.mjs --fixture .../ac-smoke-v1.json --output C:/Users/zyq/AppData/Local/Temp/ci-quality-review-ac-validate.json --run-id review-ci-quality --validate-only` | `status=VALIDATED`, exit 0；仅证明 fixture/hash/build 存在，`browser_executed=false` |
| benchmark 自测 | `python .../scripts/benchmark_hydraulic.py --fixture unused --output unused --self-test` | 4 项 PASS：valid_manifest/hash_validation/hash_drift_rejected/p95_nearest_rank |
| workflow job 依赖 | PyYAML `BaseLoader` 解析 `.github/workflows/ci.yml` | `frontend_e2e_needs=null`；workflow 无 G1～G4 job |
| acceptance config 探针 | Python 临时 JSON + `verify_hydraulic_stack._acceptance_artifact` | `mode=config accepted= True error= None` |
| G2 no-op 探针 | Python 临时 root + `gate_domain(..., "g2")`，manifest 命令为 `python -c pass` | `g2.fixtures=PASS`；`g2.harness-1=PASS` |
| G5 presence-only 探针 | Python 临时 root + `gate_g5`，solver/frontend 假脚本仅 `exit(0)`，API/nowcast 仅创建文件 | 全部 step PASS；API/nowcast `command=[]` |
| benchmark 环境顺序探针 | Python 临时合法 manifest，将 Python version monkeypatch 为 3.11.15，并清除线程/GPU 环境后调用 `validate_manifest(..., check_runtime=True)` | 首次错误 `OMP_NUM_THREADS must be exactly 4 before benchmark start` |
| ws 依赖检查 | `npm --prefix .../frontend ls ws --depth=0; npm --prefix .../frontend ls ws --all` | direct 为 `(empty)`；仅通过 `jsdom@24.0.0` 间接安装 `ws@8.21.3` |
| whitespace | `git diff --check -- .github/workflows/ci.yml` 与四个新脚本 `git diff --no-index --check` | PASS；只有 LF→CRLF 提示 |
| GitHub Actions linter | `actionlint .../ci.yml` | 未运行：`actionlint=not-installed` |

## 4. 未运行 / 不能宣称通过

本轮**没有运行**：

- GitHub Actions 远端 workflow；
- 最终全量 pytest、Vitest、lint、format、mypy、production build；
- G0/G1/G2/G3/G4/G5/G6 或最终 acceptance；
- Docker Compose config/live、PostgreSQL/PostGIS/Timescale、backup/restore；
- 真实 5000 nodes/10000 pipes/289-frame solver benchmark；
- 真实 API/nowcast 30-cycle benchmark；
- 60 秒 production map 浏览器性能门禁；
- 完整 Chrome AC E2E（本轮只运行 `--validate-only`）。

当前实现摘要已记录工作树中的 G0/E2E/G5/G6 失败或 BLOCKED；本审查没有把那些历史输出升级为本轮通过证据。

## 5. 做得较好的部分

- 两个 Python harness 与两个 Node harness 均能通过语法解析。
- benchmark self-test 能验证 manifest hash 漂移拒绝与 nearest-rank P95。
- E2E fixture 的 manifest/body SHA-256 在本轮 validate-only 中一致。
- 脚本普遍采用原子 JSON 写入、sidecar hash、明确 `0/1/2/3` 退出码和 `RUNNING/PASS/FAIL/BLOCKED` 状态。
- 前端 smoke 明确自我标注 `NON_ACCEPTANCE_SMOKE`，没有把 Node 算法循环直接冒充浏览器 25fps。

这些优点不足以抵消上述证据完整性、契约兼容性和 CI 编排阻断项。
