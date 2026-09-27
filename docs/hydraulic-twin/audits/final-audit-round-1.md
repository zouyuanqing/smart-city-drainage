# 跨模块最终审计员 — round 1

> 角色：全新上下文、只读的跨模块最终审计员。职责不是复述 AC-1~AC-8 的分项结论，而是**独立复核**分项结论所依赖的系统性前提，并对安全、数据丢失、错误告警、高成本操作做独立确认。
> 判据来源：`SOURCE_PRD.md:33`（AC-1~AC-8 原文）、`SOURCE_PRD.md:23/25/27/29/31`（FR-1~FR-7 与非功能）、`PRD_SUPPLEMENT.md:483-593`（产品语义与门禁）、`IMPLEMENTATION_CONTRACT.md`（可执行契约）。
> 基线：`git -C smart-city-drainage rev-parse --short=8 HEAD` → `ca99e048`；`git status --porcelain | wc -l` → 157，`-uall` → 747（工作树非干净，本报告结论对应当前工作树）。
> 环境：`backend/.venv/Scripts/python.exe` = Python 3.11.15（命中契约要求）。

---

## 0. 结论

**系统级判定：FAIL。八个验收标准无一达成，冻结门禁自身对全部八项返回 BLOCKED。**

本轮最重要的判断是：**分项审计里反复出现的"库层通过"不等于系统通过**，而且本轮我确认了这个偏差不是记录问题，而是代码结构问题。系统存在一个共同的结构性根因（§2 X-1），它同时解释了 AC-1/AC-3/AC-5/AC-6 的失败；另有一个**门禁自身的缺陷**（X-2），使得"全绿"也不能证明 PRD 要求的双 profile Compose 证据存在。这两点都不是任何单一 AC 审计能看见的，必须跨模块审计才能看见。

同时必须记录：**这轮没有发现伪造验收**。`matrix.json` 八项 `pass_nodeids` 全为空、无一项标 PASS；散落在 `artifacts/` 下的 12 份 G6 工件**全部是 `status=FAIL`**，且都不在门禁认定的规范路径上——不存在旧轮次 PASS 工件冒充本轮证据的情况。分项审计员在证据不足时普遍选择 partial/blocked 而非 pass，这个判断纪律是成立的。

### 本轮我自己实跑的门禁（8/8）

```
$ backend/.venv/Scripts/python.exe -B scripts/verify_hydraulic_stack.py --gate acceptance --ac <ac> --evidence-dir .runtime/final-audit-r1/<ac>
ac1 exit=2 HYDRAULIC_STACK_STATUS=BLOCKED
ac2 exit=2 HYDRAULIC_STACK_STATUS=BLOCKED
ac3 exit=2 HYDRAULIC_STACK_STATUS=BLOCKED
ac4 exit=2 HYDRAULIC_STACK_STATUS=BLOCKED
ac5 exit=2 HYDRAULIC_STACK_STATUS=BLOCKED
ac6 exit=2 HYDRAULIC_STACK_STATUS=BLOCKED
ac7 exit=2 HYDRAULIC_STACK_STATUS=BLOCKED
ac8 exit=2 HYDRAULIC_STACK_STATUS=BLOCKED
```

（`--ac` 只接受单值，多值传入时只评估最后一个——这是工具行为，不是缺陷，但也说明它无法一次聚合八项。证据目录 `.runtime/final-audit-r1/` 已在审计后删除。）

---

## 1. 我独立确认的分项结论（不重复其论证，只确认成立）

| 分项结论 | 我的独立核验方式 | 结果 |
|---|---|---|
| AC-2 行为层 22 例恶意/超大输入 fail-closed | `cd backend && .venv/Scripts/python.exe -m pytest tests/acceptance/test_negative_security.py -q` | **成立**：`40 passed, 1 warning in 0.91s` |
| acceptance 目录整体绿 | `.venv/Scripts/python.exe -m pytest tests/acceptance/ -q` | **成立**：`61 passed, 1 warning in 4.55s`（与 AC-1/2/3/6/7 各自报告的计数一致） |
| AC-3 50 年设计暴雨量级失真 | `.venv/Scripts/python.exe -c generate_design_rainfall(T, 86400, 300)` | **成立且我给出更硬的判据**（见 X-3） |
| AC-8 `profile` 参数无法选中 postgis | 读 `scripts/verify_hydraulic_stack.py:933-947` | **成立，且我定位到更精确的机械根因**（见 X-6） |
| AC-7/AC-2 共同指认 `test.db` 副作用 | 读 `backend/tests/conftest.py:10,20-21` | **成立**（见 X-7） |
| AC-6 `metric_units` 无生产者 | `grep -rn metric_units --include=*.py`（app+scripts，排除 build） | **成立**：仅两处读取、零生产写入 |

---

## 2. 跨模块 findings

> 判级：**BLOCKER** = 单独即可判系统不通过；**HIGH** = 导致数据丢失/破坏性操作/严重误判；**MEDIUM** = 削弱可观测性或可维护性；**LOW** = 台账/文案精度。

### X-1【BLOCKER·系统级】持久化层从未启用，且 31/35 路由无 fail-closed 闸门

这是本轮最重要的发现，也是 AC-1/AC-3/AC-5/AC-6 失败的**共同根因**。

- `backend/app/api/hydraulic_routes.py:297` 单点设置 `self.durable_backend = False`。
- 我实跑 `grep -rn "durable_backend\s*=\s*True" --include=*.py backend/`（排除 `backend/build`）→ **零命中**。全仓库不存在任何位置把它置为 True。
- 守卫 `_require_durable_api_store()`（`hydraulic_routes.py:327-335`）在 `DEPLOYMENT_ENVIRONMENT == "production"` 且 `durable_backend` 为假时抛 503。由于该标志恒为假，**这 4 条路由在生产环境必然 503**。
- 我用脚本解析 `hydraulic_routes.py` 的全部 `@router.*` 装饰器，逐路由检查是否调用该守卫：**35 条路由中只有 4 条被守卫**——`POST /rainfall-scenarios`(L2854)、`PATCH /rainfall-scenarios/{id}`(L2913)、`POST /simulations`(L3160)、`POST /calibrations`(L3643)。**其余 31 条无守卫**。

后果是"半 fail-closed"，比全不 fail-closed 更危险：

- **写操作返回假成功**：`POST /networks/import`(L1420) 无守卫，202 + `status="queued"` 写入进程内 dict（`hydraulic_routes.py:1455-1465` 的 `hydraulic_store.imports[import_id] = {...}`）。用户认为导入已受理，实际无任何 durable job 可被 worker 消费，重启即消失。`POST /networks`(L1383) 同样如此——**这是数据丢失路径**。
- **读操作返回假空态**：`GET /forecast/latest|horizon|risk-ranking|alerts|events`(L3771/3809/3849/3878/3985) 无守卫，生产下静默返回 HTTP 200 + 空数据，而非 503。

这**直接违反契约明文**。`IMPLEMENTATION_CONTRACT.md:1754`：

> 「未 PASS 时 production 必须**不注册/不暴露** forecast endpoints，而不是返回 503 占位后继续宣称可用。」

当前代码两条路都走错：forecast 路由照常注册并返回 200 空态（违反"不暴露"）；而被 503 挡住的恰好是 rainfall/simulations/calibrations 这 4 条——这正是契约所批评的"503 占位"形态。契约要求的是**不注册**，实现给出的是**错路由上的 503**。

> 这一条同时把 AC-4 的 F-03 从"测试把空态固化为预期"提升为"实现违反契约明文"。

### X-2【BLOCKER·门禁自身】冻结门禁的 AC→gate 映射弱于冻结需求表

`scripts/verify_hydraulic_stack.py:1704-1713` 硬编码 `ac_requirements`。我把它与两份独立冻结源逐一比对：

| AC | acceptance_requirements.json | matrix.json | **脚本实际使用** | 脚本漏掉 |
|----|---|---|---|---|
| AC-1 | g1,g2,g6 | g1,g2,g6 | `("g2",)` | **g1, g6** |
| AC-2 | g2,g6 | g2,g6 | `("g2",)` | **g6** |
| AC-3 | g3,g5,e2e,g6 | g3,g5,e2e,g6 | `("g3","g5","e2e")` | **g6** |
| AC-4 | g4,g5,e2e,g6 | g4,g5,e2e,g6 | `("g4","e2e")` | **g5, g6** |
| AC-5 | g3,g5,e2e,g6 | g3,g5,e2e,g6 | `("g3","e2e")` | **g5, g6** |
| AC-6 | g3,e2e,g6 | g3,e2e,g6 | `("e2e",)` | **g3, g6** |
| AC-7 | g0,e2e,g6 | g0,e2e,g6 | `("g0","e2e")` | **g6** |
| AC-8 | g6 | g6 | `("g6",)` | （一致）|

我实跑提取：`python -c` 读 `acceptance_requirements.json` 得到上表第 2 列；`grep -n "ac_requirements" -A 20 scripts/verify_hydraulic_stack.py` 得到第 4 列。

**两份独立冻结源（JSON 与 matrix.json）互相一致且都要求 g6，脚本单方面把它从 AC-1~AC-7 全部删掉。** 判定逻辑 `:1719` 是 `status = "PASS" if not missing and not reasons else "BLOCKED"`——缺 gate 只是"不检查"，不会阻断。

直接后果：`g6` 是**唯一有真实可产出证据的 gate**（AC-8 报告实测 portable profile 的 `--gate g6 --mode live` 可 exit 0），而 postgis profile 结构性不可跑（X-6）。因此即使 g0~g5 与 e2e 全绿，AC-1~AC-7 也会在没有双 profile Compose 证据的情况下被判 PASS。**这是"系统通过"与"分模块通过"会产生实质差异的典型位置**：修好分模块不足以让门禁诚实。

AC-1(F6)、AC-3(F-4)、AC-4(F-05) 各自独立发现了这个问题的局部，我确认它是全局的，且给出了完整差异表。

### X-3【BLOCKER·错误告警/物理不成立】50 年一遇设计暴雨 ≈ 世界 24h 极值的 4 倍

根因：`backend/app/services/hydraulic/rainfall.py:1013` 与 `:1024` 的 `20.0 * period * shape`——峰值强度与重现期**线性成正比**。IDF 曲线对 T 强烈次线性，线性外推在 T=50 已完全脱离物理。

我本轮实跑（`generate_design_rainfall(T, duration_seconds=86400, step_seconds=300)`，`intensities` 为 float 列表，289 个样本）：

```
T=1    peak=     19.93 mm/h  24h_total=    160.0 mm  samples=289
T=10   peak=    199.31 mm/h  24h_total=   1600.0 mm  samples=289
T=50   peak=    996.53 mm/h  24h_total=   8000.0 mm  samples=289
T=100  peak=   1993.06 mm/h  24h_total=  16000.0 mm  samples=289
```

判据：全球有记录 24 小时降雨极值约 1.8–2 m 量级。T=50 的 8000 mm 约为其 4 倍，T=100 的 16000 mm 约为 8 倍；峰值 996 mm/h 亦无物理对应。

为什么这条是 **BLOCKER 而非"数值待调"**：AC-3 的题面、FR-4 的告警阈值、FR-6 的风险榜全部以"50 年一遇"为设计输入。阈值建立在一个错约一个数量级的强度上，则**要么所有真实降雨都触发全量告警（系统性误报），要么阈值被迫上调以适配该错误量级，从而在真实 50 年事件时给出漏报（系统性漏报）**。两种结果都使 FR-4/AC-4 的告警不可信。

补强：AC-3 已指出验收测试走的是另一条路——`test_acceptance_matrix.py:114-142` 直接提交 `intensity_pattern_mm_h=[0,2,5,...,20,...]`，`return_period_years` 只是自报值上的标签。**即 AC-3 的测试与 50 年一遇这个语义完全解耦**，通过了也不构成该语义的证据。

### X-4【BLOCKER·错误 PASS】AC-5 合格性是一个自证标签，且仓库自带测试演示了造假路径

- `ObservationPoint`（`backend/app/services/hydraulic/calibration.py:88-98`）字段为 `observed_at, target_id, value_mm, event_id, trust_level, quality, measurement, field`。**无 auth / binding / policy / source_event 字段**；`__post_init__` 只校验 `trust_level in _TRUST_LEVELS`（即只要字符串合法即接受）。
- `run_calibration` 的 `evaluator` 由调用方提供——"仿真"不必是任何真实求解器。
- **仓库自己的测试即完整造假演示**：`backend/tests/hydraulic/test_results_calibration.py:393-416` 的 `_synthetic_dataset()` 生成 `trust_level="synthetic"` 的合成数据；测试 `:465-473` 用列表推导把标签**改写为 `"external_verified"`**；随后 `:478-501` 自带 `evaluator=lambda ...`、自填 `provenance`（形如 `"b"*64` 的占位 hash），并在 `:503-504` 断言 `report.ac5_eligible is True` / `report.improved is True`。

即：**合成数据 + 洗白信任标签 + 调用方自备评估器 ⇒ AC-5 判 PASS**，且该测试当前是通过的。AC-5 判据"率定相对默认参数有量化改善"因此可以在没有任何真实观测、没有任何真实仿真的情况下被满足。这条同时命中 `IMPLEMENTATION_CONTRACT.md:2590`（trust level 由 payload 自报）与 `:2563`。

### X-5【HIGH·破坏性操作】G6 门禁可能删除开发者本机的卷

`scripts/verify_hydraulic_stack.py:933-947` 的 `_compose_command()` 构造 argv 时**不含 `--project-name`**；`run_command` 的 `:250` 是 `process_env = os.environ.copy()`（继承环境变量，但未设置 `COMPOSE_PROJECT_NAME` 时无隔离）。`gate_g6` 在 `:1484` 执行：

```
_compose_command(root, profile, "down", "-v", "--remove-orphans")
```

Docker Compose 在未指定 project name 时**默认取当前工作目录名**。因此从某个正在运行的栈的目录执行 G6 门禁，会对该 project 执行 `down -v --remove-orphans`——**删除开发者本机的容器、网络与数据卷**。这是随仓库分发的质量脚本中的破坏性默认行为。

补充：门禁自身会记录 `dirty_state`，但未把它设为门禁条件（AC-8 F8 同指此点）。我确认此风险真实存在。

### X-6【HIGH·X5/AC-8 F1 的机械根因】`profile` 参数被静默丢弃

`_compose_command(root: Path, profile: str, *arguments: str)` 接收 `profile`，但函数体（`:933-947`）**从不使用它**：返回的 argv 中两个 `--profile` 标志是硬编码的 `"production"` 与 `"monitoring"`，`-f docker-compose.yml` 也是固定根 compose。

我实跑 `grep -n "_compose_command("` 得到 6 处调用点（`:1380`、`:1389`、`:1484`、`:1494`、`:1504` 及定义），全部传入 `profile` 并全部被忽略。

这比 AC-8 F1"根 compose 没有 PostGIS 变体"的表述更精确：**即使根 compose 补上 PostGIS 变体，postgis profile 依然永远选不中**，因为选择机制根本不存在。修复必须落在 `_compose_command`（按 profile 选 `-f docker/postgres/compose.yml` 或切换镜像），补 compose 变体是必要但不充分条件。

### X-7【MEDIUM·开发期数据丢失 + 并发互毁】`test.db` 为 CWD 相对且会话末 `drop_all`

`backend/tests/conftest.py:10`：

```python
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"
```

`:20-21` 在 session fixture 收尾执行 `Base.metadata.drop_all`。因为路径相对 **CWD**：

- 从仓库根跑测试会清空 `<repo>/test.db` 的**全部表**。我确认仓库根存在 `test.db`（73728 字节）。
- 并发 pytest 进程共享同一文件 → 互相 drop，AC-7 报告的"16 failed / no such table: users"即此机制。
- 库名固定为 `test.db`，任何同目录同名文件都在射程内。

修复：改 per-session 临时路径（如 `tempfile` 下的 session 唯一文件）。

### X-8【MEDIUM·可观测性】15 个水力指标方法中 7 个无任何生产者

`backend/app/services/hydraulic/metrics.py` 定义了 15 个公开记录方法。我逐个统计生产调用点：

| 有生产者（7） | 无生产者（8） |
|---|---|
| `set_queue_state`, `set_active_slots`, `record_simulation_terminal`, `record_nowcast_terminal`, `set_active_alerts`, `observe_event_publish_lag`, `set_event_outbox_backlog`, `record_notification_delivery` | `record_simulation_started`, `record_simulation_retry`, `record_solver_fallback`, **`record_forecast_alert_created`**, **`record_forecast_alert_suppressed`**, `record_sse_queue_overflow`, `record_cleanup_pending` |

**公平地说**：非功能要求点名的三项（任务时长 / 失败率 / 队列深度）**是满足的**——`simulation_duration_seconds` 与 `simulation_failures_total` 由 `record_simulation_terminal`(`:611,618`) 喂，`simulation_queue_depth` 由 `set_queue_state` 喂，且这些不是 worker 直连，而是由 `main.py:554-570` 的 `_hydraulic_metrics_loop`（2 秒轮询 `DatabaseHydraulicMetricsSource.snapshot()`）从 durable 状态推导。**这是一个合理的设计**，`/metrics` 端点也确实暴露（`main.py:1154-1155`）。

真正的问题在于两个与 AC-4 直接相关的计数器**零生产者**：`record_forecast_alert_suppressed` 无调用点，意味着 **AC-4 核心判据"窗口内不重复"即使实现正确，也不会产生任何 Prometheus 证据**；`record_simulation_retry` 无调用点，意味着重试风暴在监控上不可见（与 FR-3"最多两次重试"的运维诉求冲突）。同理 `record_cleanup_pending`=0 使 FR-3 沙箱清理的积压不可观测。

### X-9【CORRECTION·对 AC-8 F-4 的更正】`/readyz` 是 fail-closed 的；G6 探针只断言 200 是充分的

AC-8 F-4 记录"实测 `/readyz` 返回 HTTP 200 但 body 为 `{ready:true, code:RUNTIME_NOT_READY}`，探针在其最该起作用的失败模式上失效"。我读代码后**不同意"失效"这一定性**：

- `backend/app/main.py:1392-1397`：`if not result["ready"]: raise HTTPException(status_code=503, detail=result)`——**handler 正确 fail-closed**。
- `check_runtime_readiness`（`:828-930`）：初始 `ready=False`（`:833`），依次校验 schema head、required tables、extensions、hypertable、forecast_policy、worker_heartbeats，`:924` `result["ready"] = all(required)`，任一不满足即 `reason="runtime_contract_check_failed"` 并保持 False。
- 全局 `HTTPException` 处理器（`:1453-1480`）**保留 `exc.status_code`**（`:1478` 用 `code_by_status` 映射状态码而非改写状态码）。

因此"断言 HTTP 200"对 `/readyz` 是**充分**的判据，不存在 fail-open。

真实（且严重度低得多）的缺陷是：`code: "RUNTIME_NOT_READY"` 在 `:834` 被设为**初始静态标签且成功路径从不更新**，所以成功响应会读作 `{"ready": true, "code": "RUNTIME_NOT_READY"}`。这是**契约清晰度缺陷**，会误导按 `code` 判读的运维或监控——但不影响 fail-closed 语义。

同理，`schema.code = SPATIAL_BACKEND_UNAVAILABLE` 在 portable profile 下是预期行为（该 profile 刻意不装 PostGIS），不构成 readiness 失败。

**这条更正的实际影响**：AC-8 F-4 应从"探针失效"降级为"响应体 `code` 标签误导"，且不应作为 G6 的阻断理由。AC-8 判 FAIL 的两条阻断（F1 postgis、X-2/backup-restore 未接线）**不受影响**。

### X-10【LOW·台账精度】分项报告指认的台账过期项，集中登记

- `docs/hydraulic-twin/CONTRACT_INDEX.md:63` 记 AC-1 = `NOT RUN`；`matrix.json` 同为 `NOT RUN`。两者自洽，但 AC-1 审计员主张应为 `BLOCKED`。两种状态都合法（`IMPLEMENTATION_CONTRACT.md:2541` 允许四态），差别在汇报口径——建议统一后**不得**改成 PASS。
- `CONTRACT_INDEX.md:64` 对 AC-2 的理由文本（"Windows 盘符仍被降级为 basename；重复 stable key 被接受"）已被 AC-2 审计员实测推翻（`status: BLOCKED` 本身仍正确，但**理由已失真**）。
- `IMPLEMENTATION_CONTRACT.md:632/2499` 称 `verify_hydraulic_stack.py` 未交付——我确认该文件存在（67118 字节，可运行，本轮 8 次调用 exit 2）。该记载失效。
- `VALIDATION_EVIDENCE.md:209` 记 acceptance pytest "7 failed / 57 passed"——我本轮实跑 `61 passed`，该条对当前工作树已过期（AC-1/AC-3/AC-5/AC-7 各自也记录了类似过期项）。

**正面记录**：上述过期全部是"把已修好的事说成没修好"或"把存在说成不存在"，**方向是过度悲观，不是过度乐观**。这与 X-4（AC-5 可造假 PASS）形成鲜明对比——本轮所有造假风险都指向"更容易判通过"，所有文档漂移都指向"更容易判不通过"。这个不对称本身是可信度良好的信号。

---

## 3. FR-1~FR-7 与非功能：跨模块覆盖判定

| 需求 | 判定 | 依据 |
|---|---|---|
| **FR-1** 管网数据层 | **未达成** | 库层 parser/exporter/validate_topology 真实（AC-1 实测 64 passed，round-trip 与拓扑报告均正确）；但 API 交付面断裂：物化函数为死代码、指纹口径不一致、报告恒为 None（AC-1 F1/F2/F3，本审计确认 X-1 为其共同根因） |
| **FR-2** 降雨 | **部分达成** | 我实跑确认 `ALLOWED_RETURN_PERIODS = (1,2,3,5,10,20,50,100)`（`rainfall.py:29`）**与 PRD 完全一致**，1/5/15min 步长与 72h 自定义有校验（`:253-272`）。**但 50 年量级物理不成立（X-3）**，故"重现期"语义未真正交付 |
| **FR-3** 仿真 | **未达成** | 帧栅格 289/300s 真实推导、任务仓库契约完整；但 worker finalize 不落库、进度无生产者、benchmark 落库标志硬编码 True（AC-3 F-1~F-3） |
| **FR-4** 预测告警 | **未达成** | 物化层不存在（runner 按设计 exit 4）、全仓无 INSERT 进 forecast_alerts/event_outbox、读路径无守卫返回 200 空态并违反 `IMPLEMENTATION_CONTRACT.md:1754`（AC-4 F-01~F-03，本审计以 X-1 升级为契约违反） |
| **FR-5** API | **未达成** | 35 路由中 31 条无 durable 闸门（X-1）；写操作返回假成功属数据丢失路径 |
| **FR-6** 前端 | **未判定** | 时间轴 19 项单测通过、且前端不重算 delta（AC-6 实测 11 passed）；但 25fps 门禁与真实后端时间轴 e2e 未在本轮任何路径产出。**归入 notCovered，不作通过或失败判定** |
| **FR-7** 部署数据 | **部分达成** | **正面**：`docker/init-db.sql` 仅做扩展引导并显式声明"Alembic 是业务 schema 唯一写入方"，与迁移分工清晰；AC-8 实测 39 张表迁移至 0008；无 `:latest`、无硬编码 secret。**负面**：postgis profile 结构性不可用（X-6） |
| **非功能·180s/4 核** | **部分达成** | AC-3 实测 `worker_wall_time_max_seconds=81.56`（阈值 180）PASS；但 32 逻辑核未设 affinity、peak RSS 未采集、落库标志硬编码（AC-3 F-3/F-6/F-8） |
| **非功能·Prometheus** | **达成（字面）** | `/metrics` 已暴露；三项点名指标由 2s DB 快照轮询真实喂数（X-8 详述 7 个缺生产者的方法） |
| **非功能·Alembic 可回滚** | **未判定** | 迁移链到 0008 存在；回滚路径本轮未验证 → notCovered |
| **非功能·零回归** | **不可判定** | AC-7 已指出无改动前基线工件；本轮同样无基线 |

---

## 4. verified（本审计亲自执行）

| # | 检查 | 命令 / 方式 | 结果 |
|---|---|---|---|
| 1 | 8 项冻结门禁 | `verify_hydraulic_stack.py --gate acceptance --ac <ac>` ×8 | 全部 `BLOCKED` / exit 2 |
| 2 | `durable_backend` 置 True 的位置 | `grep -rn "durable_backend\s*=\s*True" --include=*.py backend/` | **零命中** |
| 3 | 路由守卫覆盖率 | 自写脚本解析 `@router.*` + 检查守卫调用 | 35 路由 / 4 守卫 / **31 无守卫** |
| 4 | 门禁 AC→gate 映射差异 | 读 `:1704-1713` vs `acceptance_requirements.json` + `matrix.json` | 7/8 AC 不一致，脚本缺 g6 |
| 5 | 负向安全用例 | `pytest tests/acceptance/test_negative_security.py -q` | **40 passed** |
| 6 | acceptance 全量 | `pytest tests/acceptance/ -q` | **61 passed** |
| 7 | 50 年降雨量级 | `generate_design_rainfall(T,86400,300)` | T=50 → **8000 mm/24h**，峰值 996.53 mm/h |
| 8 | FR-2 重现期集合 | `rainfall.py:29` | 与 PRD 逐项一致 |
| 9 | 上传路径控制 | 读 `hydraulic_routes.py:1040-1073` | 流式 20MiB 硬限、扩展名白名单、magic byte、严格 UTF-8；**全量驻留内存** |
| 10 | 证据目录越界防护 | 以 `--evidence-dir $TEMP/...` 调用门禁 | 正确拒绝："evidence directory escapes repository root" |
| 11 | 散落 G6 工件真伪 | 逐个解析 12 份 `result.json` 的 status | **全部 FAIL**，无一声称 PASS，均不在规范路径 |
| 12 | `/readyz` 语义 | 读 `main.py:1392-1397`、`:828-930`、`:1453-1480` | fail-closed 正确（更正 X-9/AC-8 F-4） |
| 13 | 指标生产者 | 15 个方法逐个统计生产调用点 | 7 有 / 8 无 |
| 14 | `profile` 参数使用 | 读 `:933-947` + 6 处调用点 | 参数被静默丢弃（X-6） |
| 15 | 台账无伪造 | 读 `matrix.json` 八项 | `pass_nodeids` 全空，无一项 PASS |
| 16 | FR-7 schema 分工 | 读 `docker/init-db.sql` | 扩展引导与 Alembic 分工清晰 |
| 17 | 工作树无副作用 | 清理后 `git status --porcelain \| wc -l` | 157 / 747，与审计前一致 |

## 5. notCovered（本轮未执行，不得读作通过）

**门禁与工件**：g0、g1、g2、g3、g4、g5 的完整入口；`--gate g6` 的 postgis/portable live 全量重跑（本轮仅复核既有工件真伪，**未重跑** AC-8 的 live 流程）；backup→destroy→restore 实际往返（AC-8 未接线，本轮也未手工替代——该往返含 `down -v`，手工执行不构成 G6 证据且有破坏性）。

**运行环境**：Node 18.20.8 / npm 10.8.2 上的任何前端命令（本轮未跑前端）；真实 `npm ci`（本机 Node 版本触发 `engine-strict` 硬失败，且真跑会先删 `node_modules`）；4 物理核环境重跑；真实 EPA SWMM 二进制与 `.out` 解析；live PostgreSQL/TimescaleDB 的 lease/fencing/attempt 耗尽；live InfluxDB；Windows 进程级 SIGTERM；多副本部署下的 in-process 状态可见性。

**规模与性能**：万级管段千级多边形 25fps 门禁；G5 的 API/nowcast/前端浏览器三套 benchmark（本轮未跑，AC-4 已确认所需脚本 `benchmark_hydraulic_api.py` / `benchmark_hydraulic_nowcast.py` 不存在）；`e2e/run-ac-smoke.mjs` 全量。

**判定不可得**：「旧行为改动前后零回归」（无改动前基线工件）；迁移回滚路径；PostGIS profile 下的任何行为（配置阶段即失败）。

**未复核的分项论证**：AC-1~AC-8 各自报告内部的细粒度论证（如具体 nodeid、逐条 fixture sha256 比对）本轮未逐条重跑，仅复核其依赖的系统性前提与关键断言。分项报告结论未被本审计推翻的部分，我按其自述采信但**不背书**。

**副作用声明**：本审计为只读。仅写入本文件。临时诊断脚本写在仓库外（`%TEMP%`）。门禁证据目录 `.runtime/final-audit-r1/` 已删除。审计开始时误建的 `nul` 空文件已删除。未修改任何实现、测试、契约、fixture 或证据文档。

---

## 6. 修复顺序建议（跨模块，非按 AC 排列）

按"先修根因、再补证据"排列，**跨 AC 的杠杆顺序与单 AC 顺序不同**：

1. **X-1**（durable_backend 接线 + 守卫覆盖全部写/读路由）——它单独决定 AC-1/3/5/6 能否从"库层通过"变成"系统通过"。在此之前修任何单 AC 的下游接线都只是把空系统包得更完整。
2. **X-2**（门禁映射改为从 `acceptance_requirements.json` 读取，消除第三份真相）——必须在任何 AC 被重跑判 PASS **之前**修好，否则会产出错误的 PASS 记录。
3. **X-5 / X-6**（`--project-name` 隔离 + profile 真正生效）——X-5 是破坏性风险，应最优先；X-6 是 AC-8 乃至 X-2 中 g6 的前置。
4. **X-3 / X-4**（IDF 曲线查表化 + 封死 trust 自报）——两条都是"更容易判通过"的造假风险，与前几条的"更严"方向相反，不应因修复成本被推后。
5. **X-7 / X-8 / X-9 / X-10**（测试库隔离、指标补齐、`code` 标签修正、台账对齐）。

**关于重跑门禁**：在 1–3 完成前，即使 g0~g5 与 e2e 全绿，`--gate acceptance` 也不足以支撑任何 AC 的 PASS 结论——这一点必须写进下一轮的验收前置条件，否则下一轮会重复本轮的分模块/系统判定错位。
