# delivery-docs 独立审查（第 7 轮）

> 审查时间：2026-09-28。审查者**只读**：未修改任何被审文件，未 commit、未 push。
> 被审对象：`README.md`、`docs/hydraulic-twin/operations.md`、`docs/hydraulic-twin/CONTRACT_INDEX.md`、
> `docs/hydraulic-twin/implementation/delivery-docs.md`（第 7 轮交付摘要）。
>
> **本审查唯一的写入**：本文件（上一轮的第 6 轮审查被本文件覆盖，这是 ask 指定的输出路径）、
> 两处门禁证据目录 `.runtime/evidence/rev-dd-r8-{portable,postgis}/`（见 §1.5），
> 以及仓库外的 `/tmp/clean.env`、`/tmp/clean2.env`、`/tmp/empty.env`、`/tmp/rainfall-50y.json`、`/tmp/cleanclone/`。
> 仓库工作树内除本文件外没有新增/修改任何文件（`git status` 复核见 §1.7）。

---

## 0. 结论

**verdict：APPROVED —— 无阻断项、无高风险项；10 条 medium + 3 条 low 建议本轮内修完。**

判定依据：本模块最要紧的属性是"不把未验证项写成已通过"。这一点**经独立复现成立**——
readiness 阻断解除、演示数据为空、G6 portable PASS / postgis FAIL、干净 env 配方 exit 0、
12/13/14/15 服务集合、端口与 expose、50 年 289 点生成器，全部由本审查**亲手重跑**并与文档逐字对上；
`NOT RUN` 清单也真实覆盖了未执行项。**本审查没有找到任何把未验证项写成已通过的地方，
也没有找到会让人误判发布结论的阻断项。**

但"证据指针"这一层没有做到与正文同级：4 处 `path:line` 落在错误行（含 1 处同段自相矛盾）、
1 处容器计数与原始观测矛盾、1 处 README 内部对同一症状给出两个相反根因、1 处 Compose 读取 `.env`
的机制被写错、1 处 G6 PASS 缺前提。这些都不改变"演示跑不通"的结论，但正是本模块自己在
`CONTRACT_INDEX.md` §8 第 10/13/14 条里禁止的错误类型。

---

## 1. 本轮实际执行的检查

ask 指定的四个 test 跑了三个；第四个（`verify_hydraulic_stack.py`）按 ask"不要重复最终全量门禁"的
要求，只重跑了 `--gate g6 --mode config` 的两个 profile（定向诊断，不含 live / G0～G5 / acceptance）。

### 1.1 `.runtime/doc_checks.py`（ask 指定）

```text
$ python -B .runtime/doc_checks.py ; echo DOC_CHECKS_EXIT=$?
WS_FILES_CHECKED=4
WS_PROBLEMS=0
LINKS_MISSING=none
BASH_SYNTAX_FAILURES=none
PACKAGE_MISSING=none
BASH_PLACEHOLDERS=none
UNEXPORTED_VARS=none
ROUTE_CITATION_MISMATCH=none
CITATIONS_OUT_OF_RANGE=none
DOC_CHECKS_EXIT=0
```

三个计数键的实测值（摘要 §2.2 明确说"不要引用本表"，此处只作本审查自用记录，不构成对文档的指控）：
`BASH_BLOCKS_CHECKED=48`、`CITATIONS_CHECKED=161`、`ROUTE_ROWS_CHECKED=43`。
`BARE_NPM_RUN_IN_BASH_BLOCK` 仍命中 README 那一行——摘要 §2.2 把它判为检查器假阳性，
**本审查复核属实**：`.runtime/doc_checks.py:119` 的条件确实是
`... and "cd frontend" not in match.group(1)`。
`CITATIONS_OUT_OF_RANGE=none` 只验"行号 < 文件行数"，不验指向——本审查因此逐条 `sed -n` 核对了
散文引用，才发现了 MEDIUM-1/2。

### 1.2 `.runtime/route_dump.py`（ask 指定）

`ROUTE_DUMP_EXIT=0`，`TOTAL=98 API_TOTAL=68 ROOT_TOTAL=8`；
`GET /health → backend/app/main.py:1401`、`GET /readyz → backend/app/main.py:1392`、
`GET /metrics → …/prometheus_fastapi_instrumentator/instrumentation.py:259`。与文档一致。

### 1.3 `.runtime/api_cmd_check.py`（ask 指定）

`DOC_API_PATHS_CHECKED=31`、`DOC_API_PATHS_UNMATCHED=none`、`API_CMD_EXIT=0`。与文档一致。

### 1.4 干净环境 compose 配方（README §1 逐字照抄）

```text
$ cp .env.example "$CLEAN_ENV" && cat >> "$CLEAN_ENV" <<'ENV' …（README:271-279 那 7 行）… ENV
$ docker compose --env-file "$CLEAN_ENV" --profile production --profile monitoring \
    -f docker-compose.yml config --quiet
clean-env exit=0
$ 逐个 profile 统计 config --services
profiles=[] services=12 ; profiles=[production] services=13 ;
profiles=[monitoring] services=14 ; profiles=[production --profile monitoring] services=15
```

**README §1 的服务数量表 12/13/14/15 全部复现。**

`config --format json` 的 `ports`/`expose` 实测：`backend 8000`、`frontend 3000→8080`、
`grafana 3001→3000`、`nginx 8080`、`prometheus 9090`；`expose` 为
`8010/8021/8022/8023/8030/8024`（含 `notification-sink 8030`）。
与 `README.md:206-209`、`operations.md:34`、`CONTRACT_INDEX.md:212-216` 一致。

两个负样本：

```text
# 不给 GRAFANA_ADMIN_PASSWORD、且不带 --profile monitoring
error while interpolating services.grafana.environment.GF_SECURITY_ADMIN_PASSWORD:
  required variable GRAFANA_ADMIN_PASSWORD is missing a value: set GRAFANA_ADMIN_PASSWORD
# → "整文件插值、该变量不 profile 也拦"的结论成立

# --env-file 指向空文件
error while interpolating services.redis.environment.REDIS_PASSWORD:
  required variable REDIS_PASSWORD is missing a value: set REDIS_PASSWORD
empty-env exit=1        # exit 1 成立；首个被点名的变量与文档逐字引用的不同，见 LOW-3
```

### 1.5 G6 静态门禁（`scripts/verify_hydraulic_stack.py`，ask 指定，只跑 config 模式）

```text
$ python -B scripts/verify_hydraulic_stack.py --gate g6 --mode config --profile portable \
    --evidence-dir .runtime/evidence/rev-dd-r8-portable
HYDRAULIC_STACK_STATUS=PASS
HYDRAULIC_STACK_EXIT=0
"static_failures": []
"scope_note": "Compose config is a configuration check only; it is not live G6 or AC-8 evidence."

$ python -B scripts/verify_hydraulic_stack.py --gate g6 --mode config --profile postgis \
    --evidence-dir .runtime/evidence/rev-dd-r8-postgis
HYDRAULIC_STACK_STATUS=FAIL
HYDRAULIC_STACK_EXIT=1
```

**portable PASS / postgis FAIL 与 `operations.md` §3.3、`CONTRACT_INDEX.md` §5.2 逐字一致。**
同时直接读了被审轮留下的两份证据 `.runtime/evidence/dd-r7-{portable,postgis}/result.json`：
`status` / `static_failures` / `scope_note` / `run_id=20260927T17*Z-ca99e048`（本地 +08 即 2026-09-28 凌晨）
全部与文档相符。
> 说明：`HYDRAULIC_STACK_EXIT=` 是脚本自己打印的标记；本审查用管道取尾部时 shell 的 `$?` 是 `tail` 的
> 退出码，不作为证据使用。

### 1.6 对那台"已在运行的栈"的只读复核

```text
$ docker ps -a --filter name=smart-city-drainage --format '{{.Names}}\t{{.Status}}'
… backend-1 / network-job-worker-1 / simulation-worker-1 / outbox-relay-1 / nowcast-worker-1 /
  notification-worker-1 / notification-sink-1 / postgres-1 / redis-1 / influxdb-1   全部 Up (healthy)
smart-city-drainage-migrate-1   Exited (0)
→ 合计 11 个容器（10 Up + 1 Exited），无 nginx / frontend

$ curl -s -w ' HTTP=%{http_code}' http://127.0.0.1:8000/health          → HTTP=200
$ curl -s http://127.0.0.1:8000/readyz
ready=True reason=None
required_tables=true extensions=true hypertables=true forecast_policy=true
worker_heartbeats={network-job:true, simulation:true, nowcast:true, outbox-relay:true, notification:true}

$ docker exec … psql -t -A -c "…"      # 只读计数
networks=1 code=SINK-TEST
revisions=0 nodes=0 pipes=0 catchments=0
rainfall=0 jobs=0 sims=0 nowcast=0
alerts=0 ab=0 calib=0 obs=0
forecast_rules=2 prob_calib=2 alembic=0008_hydraulic_retention_seed
```

**readiness 解除、演示数据为空、`SINK-TEST`、`0008` 对齐——全部与文档一致。**
（`docker ps` / `docker exec … psql SELECT` / `curl` 均为只读，未重启、未改任何容器。）

### 1.7 内容级行号复核（抽样，全部命中）

逐条 `sed -n '<行>p' <file>` 核对，与文档声称一致：
`hydraulic_routes.py:297 / 363 / 423 / 439 / 496 / 513 / 545 / 566-569 / 617 / 641 / 2578-2583 /
2788 / 2803 / 2884 / 3466-3481 / 3558 / 3578 / 3771 / 3817 / 4007-4016`；
`main.py:84-90 / 665-673 / 757 / 781 / 808-825 / 917-926 / 1155 / 1273-1284 / 1285-1298 / 1392 / 1401`；
`routes.py:80`；`sse_manager.py:637 / 645-646 / 897-910`；
`restore.py:188-197 / 722-726 / 736-738 / 823-864`；`backup.py:765 / 786-789`；
`simulation_worker.py:1880 / 1924`；`verify_hydraulic_stack.py:44-58`；
`docker-compose.yml:9 / 180-181 / 250 / 251-252 / 268 / 283 / 356 / 513 / 690-692 / 742-744 / 778`；
`.gitignore:31 = .env`；`frontend/.node-version = 18.20.8`；`frontend/package.json:7-10` engines；
`frontend/src/App.tsx:57-108` 的 9 条路由。**唯一两处例外见 MEDIUM-1 / MEDIUM-2。**

边界复核：`git rev-parse --short=8 HEAD → ca99e048`；`git show HEAD:./docker-compose.yml | wc -l → 190`；
工作树 `docker-compose.yml` 802 行、`backend/app/main.py` 1521 行；`git ls-files docs | wc -l → 0`；
`git ls-files .env | wc -l → 0`。与 `delivery-docs.md` §6 逐项一致。

### 1.8 可复制命令的实跑

README/operations §5.1 的 289 点生成器（只把输出路径改到仓库外的 `/tmp`，仓库内未建目录）：

```text
$ python - "$NETWORK_ID" > /tmp/rainfall-50y.json <<'PY' …手册原文… PY
heredoc exit=0 → points 289
first {point_index:0, valid_at:'2026-09-25T00:00:00Z'}
last  {point_index:288, valid_at:'2026-09-26T00:00:00Z'}
content_sha256 长度 64
```

逐条比对 `RainfallScenarioRequest` 的校验器（`hydraulic_routes.py:455-495`）：
`step_seconds=300 ∈ {60,300,900}` ✓；`289 == 86400//300+1` ✓；`point_index` dense ✓；
`valid_at` 严格递增 ✓；`intensity_mm_h >= 0`（fixture pattern 首尾为 `0.0`，允许）✓；
`kind=design` 且 `return_period_years=50 ∈ ALLOWED_RETURN_PERIODS`（`app/services/hydraulic/rainfall.py:29`）✓；
`algorithm=synthetic-design-chicago-v1` ✓。
`_rainfall_content_hash`（`:2788-2803`）经 `_fingerprint`→`_canonical`（`:207-218`），
与文档描述的 `sort_keys=True / separators=(",",":") / ensure_ascii=False / allow_nan=False` 一致。
**§5.1 的命令与它对 hash 契约的说明都成立。**

### 1.9 本审查**没有**执行（不计入 verdict）

`docker compose build/up/down -v`、任何 fresh-volume 或 live 启动；live G0～G6、`--gate acceptance`；
§4～§9 的任何 HTTP 命令；50 年真实求解、nowcast 周期、告警落库、SSE replay、A/B、率定；
备份→销毁→恢复；Alembic live upgrade/downgrade；全量 pytest / vitest / `npm ci` / `npm run *`；
`check_migration_gate.py` / `check_dependency_handoff.py` / `verify_database.py` / `backup_test.py` /
`validate_monitoring.py`；§2.6 的 smoke-header 三用例与 §2.7 的 simulation worker 双分支
（**沿用上一轮实测，本轮未重跑**——与 `delivery-docs.md` §5 的自述一致）。

---

## 2. Findings

### MEDIUM-1 · `operations.md:157` 引用了错误的 Compose 行：`docker-compose.yml:676` 是 `security_opt:`

同一文档的 §11（`operations.md:1121`）与 README 两处（`README.md:290`、`README.md:1092`）写的是
**`docker-compose.yml:778`**，交付摘要 `delivery-docs.md:363` 的位移表也明确记录
"`GRAFANA_ADMIN_PASSWORD` 从 `:676` → `:778`"。也就是说 §3.1 那一行**漏掉了这次整体位移**，
而它恰好在"照着做"的配方块里。`doc_checks.py` 抓不到（`CITATIONS_OUT_OF_RANGE` 只验范围）。

- **问题**：注释指向的 `:676` 与它声称的内容（`${GRAFANA_ADMIN_PASSWORD:?…}`）无关；
  读者按图索骥会落在别的服务的 `security_opt` 上，并无法判断文档指针是否可信。
- **证据**：
  - `sed -n '676p' docker-compose.yml` → `    security_opt:`
  - `sed -n '778p' docker-compose.yml` → `      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD:?set GRAFANA_ADMIN_PASSWORD}`
  - 摘要自身：`docs/hydraulic-twin/implementation/delivery-docs.md:363`
    `| docker-compose.yml:676 | GF_SECURITY_ADMIN_PASSWORD | :778 |`
- **修**：把 `operations.md:157` 的 `docker-compose.yml:676` 改成 `:778`；然后按
  `CONTRACT_INDEX.md` §8 第 13 条对**全部** `docker-compose.yml:` 引用再 `grep -n` 一遍
  （本审查 §1.7 已逐条核对，除此处外全部命中）。

### MEDIUM-2 · `CONTRACT_INDEX.md:152` 同一段自相矛盾：`.expose(...)` 调用写成了 `backend/app/main.py:1146`

`:140` 的路由表写 `/metrics → backend/app/main.py:1155`（`expose` 的调用点），
`:151-152` 的注解却写"`.expose(...)` 调用在 `:1146`"。`:1146` 是 `app = FastAPI(...)` 里的
`redoc_url="/redoc"`，与 Prometheus 无关。这是**位移前的旧值**——`delivery-docs.md:370` 记录的映射
就是 `main.py:1146 → :1155`，摘要更新了路由表却漏了注解。

- **问题**：契约索引是本模块的"证据索引"交付物，同一段里两个互斥行号会让读者无法判断哪个可信；
  这正是第 10 条规则（"`path:line` 只验范围不验指向"）需要人工兜住的地方。
- **证据**：
  - `sed -n '1146p' backend/app/main.py` → `    redoc_url="/redoc",`
  - `sed -n '1155p' backend/app/main.py` → `).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)`
  - `sed -n '1151,1154p'` → `Instrumentator(` + 3 个参数行（与文档"构造在 `:1151-1154`"一致）
  - 自相矛盾的两处：`CONTRACT_INDEX.md:140` vs `CONTRACT_INDEX.md:152`
- **修**：`:152` 的 `:1146` 改为 `:1155`。

### MEDIUM-3 · `CONTRACT_INDEX.md:14` 的反向引用 `README.md:209` 与其声称的内容无关

该行声称根级路由行号"由直查源码得到（`README.md:209` 的反向声明同源）"。

- **问题**：`README.md:209` 讲的是 `postgres`/`redis`/`influxdb`/`migrate` 没有 `ports`/`expose`，
  与根级路由无关。真正对应的那句在 `README.md:679`
  （"系统 liveness/readiness/metrics 位于根路径：`/health`、`/readyz`、`/metrics`"）。
  这是 §8 第 11 条（根级路由不被 `/api` 导出覆盖）唯一的跨文档佐证，指针一断该规则就悬空。
- **证据**：
  - `sed -n '209p' README.md` → `` `postgres` / `redis` / `influxdb` / `migrate` 两者都没有。``
  - `sed -n '679p' README.md` → `业务端点前缀为 /api，… 系统 liveness/readiness/metrics 位于根路径：/health、/readyz、/metrics。`
- **修**：把 `README.md:209` 改为 `README.md:679`（或删掉这个括号里的旁证）。

### MEDIUM-4 · `CONTRACT_INDEX.md:10-11` 指向了 `delivery-docs.md` 的错误子节，并引用了该文件里根本不存在的"43 行"

原句："…见 `implementation/delivery-docs.md` §2.1；§2.2 是文档 API 路径与 live 路由的逐条对齐，
§2.1 **末尾是 43 行 method+path 的复核结果**"。

- **问题**：① 真实的 `/api` 路由导出在当前 `delivery-docs.md` 的 **§2.2**（`route_dump.py` 那一段），
  §2.1 是"起点"的三检查器输出（`DOC_CHECKS_EXIT=1` + 3 条 `ROUTE_CITATION_MISMATCH`），里面没有任何路由表；
  ② "43 行"在 `delivery-docs.md` 全文中不存在——该轮**刻意**不引用这类会随自身内容漂移的计数键
  （见 `delivery-docs.md:93-106`），而 43 正是 `doc_checks.py` 的 `ROUTE_ROWS_CHECKED`。
  契约索引因此把一个"本轮不打算固定"的数字写进了导航句。
- **证据**：
  - `docs/hydraulic-twin/implementation/delivery-docs.md:60` → `### 2.1 起点：三个检查器`；
    `:75` → `### 2.2 终点（本轮改完后重跑）`；`:123-134` 才是 `route_dump.py` 的输出
  - `grep -n '43' docs/hydraulic-twin/implementation/delivery-docs.md` → 只命中 `356/359/360/460`
    四行**行号区间**（`docker-compose.yml:356-433` 等），没有任何"43 行"的表述
  - `python -B .runtime/doc_checks.py` 本轮实测 `ROUTE_ROWS_CHECKED=43`
- **修**：`:10-11` 改为指向 §2.2；删掉"§2.1 末尾是 43 行…"整句，或改成
  "method+path 的逐条复核由 `doc_checks.py` 的 `ROUTE_CITATION_MISMATCH` 承担"这种不含计数的表述。

### MEDIUM-5 · 容器计数与本轮自己的原始观测矛盾：`operations.md:272` 与 `README.md:337`

原始观测（`delivery-docs.md:249-262`，本审查 §1.6 复核为 10 Up + 1 Exited = **11**）之后：

- **问题**：
  - `operations.md:272` 写"`docker ps` 显示 **12 个容器**（10 个 `Up (healthy)` + `migrate` `Exited (0)` +
    `notification-sink` `Up`）"——`notification-sink` 本来就在那 10 个里，**被重复计了一次**；
  - `README.md:337` 写"`docker ps` 的 **12 个容器全部 healthy**、`migrate` `Exited(0)`"——
    `docker ps` 只显示运行中的容器（实测 10 个），已退出的 `migrate` 不在其中，按这句读是 13 个；
  - `operations.md:1199-1200` 写"10 个容器 `Up (healthy)`，`migrate-1` `Exited (0)`"——**这一处是对的**。
  三处口径不一致，且两处错误的都在"本轮实测观测"这种最不该出错的段落。
- **证据**：
  - `delivery-docs.md:250-259`：`docker ps` 逐行列出 10 个容器，`notification-sink-1` 在其中
  - `delivery-docs.md:260-261`：`docker ps -a` 追加 `migrate-1  Exited (0)`
  - 本审查实测 `docker ps -a --filter name=smart-city-drainage` → 恰好 11 行
  - 冲突行：`docs/hydraulic-twin/operations.md:272`、`README.md:337`；
    正确口径 `docs/hydraulic-twin/operations.md:1199-1200`
- **修**：两处统一成"10 个容器 `Up (healthy)` + `migrate-1` `Exited (0)`，合计 11；
  该栈未起 `production` profile"。

### MEDIUM-6 · README 内部对同一症状给出两个相反根因：`README.md:976` 的过期结论没被清掉

- **问题**：`README.md:971-980`（"容器编排"）的注释仍写着
  "包含前端（生产模式；**注意当前 backend/worker healthcheck 下不会真的起来**，见"快速开始"）"。
  而本轮重写后的"快速开始"（`README.md:164-192`）与 FAQ 0（`README.md:1059-1090`）给出的结论正好相反：
  `/readyz` 200、backend healthy，**没起的是 `production` profile**，不是 healthcheck。
  读者从目录页点进"容器编排"会拿到已被本轮自己推翻的根因。
  同一块还继续用 `--env-file .env`（`:971/974/977/980`）指向那份被 `.gitignore:31` 忽略的 `.env`，
  与 FAQ 0.1"**`cp .env.example .env` 不够**"的结论相冲突。
  这正是本轮新写的 `CONTRACT_INDEX.md:405-413`（规则 14："过期的'失败'结论与夸大成功同样有害"）
  要消灭的类型，只是漏了同一文件里的这一处。
- **证据**：
  - `sed -n '976p' README.md` → `# 包含前端（生产模式；注意当前 backend/worker healthcheck 下不会真的起来，见"快速开始"）`
  - `sed -n '1059,1063p' README.md` → "先确认一件事：**这两个地址需要 `--profile production`**"，
    并注明观测到的那台栈 "backend 与 6 个 worker 全 healthy，但这两个容器不存在"
  - `sed -n '1095,1098p' README.md` → "⚠️ **`cp .env.example .env` 不够。**"
- **修**：`:976` 改成"需要 `--profile production`；2026-09-28 观测到的那台栈没起该 profile"；
  `:971-980` 的四条命令统一改用 `--env-file "$CLEAN_ENV"`（与 §1 配方同源），
  或在块首写明"前提是 `.env` 已按 §1 补齐"。

### MEDIUM-7 · 三份文档都把 Compose 读 `.env` 的机制写错了：读的是**项目目录**，不是工作目录

- **问题**：`README.md:251-253`、`operations.md:168-169`、`CONTRACT_INDEX.md:386-388` 都写
  "`docker compose` 会**自动读取工作目录下的** `.env`"。实测不是：Compose 默认从
  **项目目录**（第一个 `-f` 所在目录，或 `--project-directory`）读 `.env`。
  结论（"干净 clone 上必须用 `--env-file`"）仍成立，但给出的理由是错的，
  而这个理由正是本模块"干净环境配方"整套叙述的立论基础。
- **证据**：
  - `cd /tmp/cleanclone && docker compose --profile production -f "<repo>/docker-compose.yml" config --quiet`
    → **exit 0**（工作目录没有 `.env`，仍拿到了仓库根那份 `.env` 里的变量）
  - 同上加 `--project-directory /tmp/cleanclone` → **exit 1**
    （`required variable DATABASE_URL is missing a value: set DATABASE_URL to the encoded PostgreSQL URL`）
  - 把 `docker-compose.yml` 复制到无 `.env` 的 `/tmp/cleanclone/dc.yml` 再 `config --quiet` → **exit 1**
  - 文档原文：`README.md:251-253`、`docs/hydraulic-twin/operations.md:168-169`、
    `docs/hydraulic-twin/CONTRACT_INDEX.md:386-388`
- **修**：三处把"工作目录"改成"**项目目录**（默认是 `-f` 指向的 compose 文件所在目录，
  可用 `--project-directory` 改）"，并补一句"因此在子目录里执行 `docker compose -f ../docker-compose.yml`
  同样会读到仓库根的 `.env`"。

### MEDIUM-8 · "G6 portable 静态项已无缺口"没有写前提：门禁自己的 compose 调用**不带** `--env-file`

- **问题**：`operations.md:31`、`operations.md:226-235`、`CONTRACT_INDEX.md:218-233` 把
  `--gate g6 --mode config --profile portable` 的 exit 0 写成"已无缺口"，
  而三份文档同时反复强调"本机未跟踪的 `.env` 会掩盖缺失的必填变量"。
  但门禁脚本自己执行的 `docker compose … config --quiet` **根本没有 `--env-file`**
  （见证据里的 `steps[0].command`），也就是这个 PASS 成立的条件恰恰是"项目目录里有那份 `.env`"。
  在真正干净的 clone 上同一条命令会 exit 1。这正是 `CONTRACT_INDEX.md:386-393`（规则 12）
  要求"exit 0 必须写清前提"的场景，而这一处没写。
- **证据**：
  - 本轮运行产物 `.runtime/evidence/rev-dd-r8-portable/result.json` 的 `steps[0].command` =
    `["docker.EXE","compose","--profile","production","--profile","monitoring","-f","docker-compose.yml","config","--quiet"]`
    ——无 `--env-file`；被审轮留下的 `dd-r7-portable/result.json` 同上
  - 对照实验：同一 compose 文件放在没有 `.env` 的目录里 `config --quiet` → exit 1（§1.4/§1.7）
  - 文档原文：`docs/hydraulic-twin/operations.md:31`、`:226-235`；`docs/hydraulic-twin/CONTRACT_INDEX.md:218-233`
- **修**：这三处补一句限定——"该 PASS 是在**工作树存在未跟踪 `.env`** 的前提下取得的；
  脚本的 compose 调用不带 `--env-file`，在干净 clone 上需先按 §3.1 构造合成 env，否则同一条命令会 exit 1"。

### MEDIUM-9 · 门禁记录的 artifact sha256 在 Windows 上无法复现（跨模块；文档未提示）

- **问题**：`delivery-docs.md:223`/`:231` 引用了 `58d1187f…`（portable）与 `a233accf…`（postgis）
  作为 artifact sha256，这两个值**忠实抄自脚本 sidecar**，文档没有写错。
  但 `atomic_write_json`（`scripts/verify_hydraulic_stack.py:319-328`）先对
  `json.dumps(...) + "\n"` 算 sha256，再用 `Path.write_text` 落盘——Windows 上 `write_text`
  默认把 `\n` 翻译成 `\r\n`，于是**落盘字节的哈希与 sidecar 记录永远不相等**。
  `CONTRACT_INDEX.md:358`（规则 2）要求"写通过时记录 artifact path/hash"，
  读者按这条去核对必然对不上，且无法分辨是篡改还是换行。
- **证据**：
  - `sha256sum .runtime/evidence/dd-r7-portable/result.json` → `616f208f73ef…`
    而 sidecar `result.json.sha256` → `58d1187f3a41…`（**不等**）
  - `dd-r7-postgis`：`773b20d64ea8…` vs sidecar `a233accf37c1…`（**不等**）
  - 本审查自己新跑的一份：报告值 `e280d1cd257c…` vs 落盘 `688ac05bcee0…`（**不等**，系统性复现）
  - 根因：`scripts/verify_hydraulic_stack.py:319-328`（对 `payload` 算哈希 vs `write_text` 落盘）
    与 `:1691-1694`（`finalise_report` 把 `file_hash` 写进 sidecar）
  - 受影响文本：`docs/hydraulic-twin/implementation/delivery-docs.md:223`、`:231`；
    规则出处 `docs/hydraulic-twin/CONTRACT_INDEX.md:358`
- **修（文档侧，本模块可做）**：在被引用的 artifact hash 旁注明"该 hash 由脚本在写入前计算；
  Windows 上落盘换行为 CRLF，`sha256sum result.json` 会对不上"。
  **（脚本侧，scripts owner）**：`write_text(..., newline="\n")` 或改为对落盘字节算哈希，
  并补"sidecar 哈希 == 落盘文件哈希"的回归断言。

### MEDIUM-10 · README 的启动主命令没带 `--env-file`，在干净 clone 上照抄必失败

- **问题**：`README.md:155-158` 的"启动基础设施 + 后端 + Nginx"是全篇启动主命令，
  却是唯一一条不带 `--env-file` 的 compose 命令——同文件 `:214`、`:221`、`:224`、`:227`、`:325-330`、
  `:1065-1066` 全都带了。而 §1 的配方（`README.md:260-288`）**只把 13 个剩余变量写进 `$CLEAN_ENV`
  这个文件，并没有 export**，所以照目录从上往下走：§1 exit 0 → 下一条 `up` 在没有 `.env` 的干净
  clone 上直接 exit 1。这与本轮花 §1 + FAQ 0.1 专门警告的失败模式是同一种。
- **证据**：
  - `sed -n '155,158p' README.md` → `docker compose --profile production --profile monitoring \` /
    `  -f docker-compose.yml up --build -d`（无 `--env-file`）
  - `sed -n '271,279p' README.md` → 补齐块全部写进 `"$CLEAN_ENV"`，**无任何 `export VAR=`**
  - 等价实验：把同一 compose 文件放到没有 `.env` 的目录执行不带 `--env-file` 的 `config --quiet`
    → **exit 1**，`required variable POSTGRES_PASSWORD is missing a value`
  - 对照：`README.md:1065-1066`（FAQ 0 的"正确起法"）是带 `--env-file "$CLEAN_ENV"` 的
- **修**：给 `:156` 补 `--env-file "$CLEAN_ENV"`，与同文件其它 6 条命令统一。

### LOW-1 · `README.md:1002-1004` 的 worker expose 列表漏了 `notification-sink 8030`，取数日期也过期

同一份 README 的"访问地址"段（`README.md:207-209`，2026-09-28 取数）写的是 **6 个** worker 且含
`notification-sink 8030`；而"端口映射"段仍写"**5 个** worker 只用 expose"并只列 5 个端口。
两处自相矛盾，且本段取数日期停在 `README.md:989` 的"2026-09-27 核对"。

- **证据**：`sed -n '1002,1004p' README.md` → `5 个 worker 只用 expose…notification-worker 8024`（无 8030）；
  `sed -n '989p' README.md` → `…（2026-09-27 核对）。`；
  实测 `config --format json` 的 expose 含 `notification-sink ['8030']`（§1.4）
- **修**：补 `notification-sink 8030` 并改成 6 个；日期更新或删除（同文件已有 2026-09-28 的取数说明）。

### LOW-2 · "`.env.example` 里有 6 个必填变量是空值"与 README 的"7 项"都不准确

- **问题**：14 个必填变量里，`.env.example` **只有 `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` 两项是"存在但为空"**；
  `POSTGRES_PASSWORD`、`GRAFANA_ADMIN_PASSWORD`、`INFLUXDB_PASSWORD`、`REDIS_PASSWORD`
  在 `.env.example` 里**根本不存在**（不是空值）。README §1 还把 `REDIS_URL` 算进"留空的 7 项"，
  但它在 `.env.example:104` 有非空值（指向 `localhost`），那是"需要覆盖"而不是"留空"。
  运维结论（`cp .env.example .env` 不够）不受影响，但按名去 grep 会一无所获。
- **证据**：
  - 对 14 个必填变量逐个取值：`JWT_PRIVATE_KEY empty=YES`、`JWT_PUBLIC_KEY empty=YES`；
    `DATABASE_URL`/`DATABASE_URL_SYNC`/`JWT_ISSUER`/`JWT_AUDIENCE`/`INFLUXDB_TOKEN`/
    `INFLUXDB_ORG`/`INFLUXDB_BUCKET`/`REDIS_URL` 均 `empty=no`
  - `grep -n 'POSTGRES_PASSWORD\|GRAFANA_ADMIN_PASSWORD\|INFLUXDB_PASSWORD\|REDIS_PASSWORD' .env.example`
    → **无输出**（四项未出现）
  - 受影响文本：`README.md:258`、`:268`、`:1095-1097`；`operations.md:32`、`:197`；
    `CONTRACT_INDEX.md:392`
- **修**：统一改成"14 个必填变量中，`.env.example` 有 **2 个留空**、**4 个未提供**
  （`POSTGRES_PASSWORD`/`GRAFANA_ADMIN_PASSWORD`/`INFLUXDB_PASSWORD`/`REDIS_PASSWORD`），
  另需把 `REDIS_URL` 从 `localhost` 覆盖成 `redis`"；README §1 的"7 项"改成"补 6 项 + 覆盖 1 项"。

### LOW-3 · 空 `--env-file` 的报错被当"原始输出"逐字引用，但变量名对不上

- **问题**：`operations.md:190-192` 与 `delivery-docs.md:169-174` 把
  `required variable DATABASE_URL is missing a value: set DATABASE_URL to the encoded PostgreSQL URL`
  当作实测原始输出贴出（后者还归到 `services.network-job-worker.environment.DATABASE_URL`）。
  本轮同一条命令报的是 `services.redis.environment.REDIS_PASSWORD`。
  **"空文件 = 零变量 → exit 1"的结论成立**（本轮复现 exit 1）；
  只是"第一个被点名的变量"取决于遍历顺序，不是稳定契约。
- **证据**：
  - `docker compose --env-file /tmp/empty.env --profile production -f docker-compose.yml config --quiet`
    → exit 1，首行 `error while interpolating services.redis.environment.REDIS_PASSWORD: …`
  - 文档原文：`docs/hydraulic-twin/operations.md:190-192`；
    `docs/hydraulic-twin/implementation/delivery-docs.md:169-174`
- **修**：把逐字引用改成"实测 exit 1，报 `required variable <X> is missing a value`（X 取决于遍历顺序，
  本轮是 `REDIS_PASSWORD`）"。

---

## 3. 值得肯定的地方（影响判断，不构成背书）

1. **"不把未验证项写成已通过"是真的**。`operations.md` §13 与 `delivery-docs.md` §5 把
   `docker compose up/down -v`、全部 HTTP 写操作、live G0～G6、pytest/vitest/npm、
   备份恢复、50 年真实求解逐条列为未执行，`README.md:245-247` 在正文里复述了一次。
   本审查未找到任何一处把 NOT RUN 写成 PASS。
2. **过期的"失败"结论被对称清理**（`/readyz` 503、`forecast_policy`、根 Compose 缺 worker、
   `--env-file` 空文件），并配了"若又看到先怀疑旧镜像"的可操作指引
   （`operations.md:324-331`、`:1125`；`README.md:1082-1086`）。这是本轮最大的实质改进。
3. **行号纪律大部分是真的**：本审查抽样 60 余处 `path:line`，除 MEDIUM-1/2 外全部内容级命中
   （含 `hydraulic_routes.py:2788-2803` 的 10 key 指纹、`main.py:665-673` 的入参签名、
   `restore.py:722-726` 的 `headers[env_name] = value` 这类容易写错的点）。
4. **计数漂移问题被根治**：摘要 §2.2 只列 8 个不变量（违规计数），并明说"不要引用本表"——
   本审查实测 `CITATIONS_CHECKED=161`（该轮自报"约 160"），确认它确实会漂，这个处理是对的。
5. **可复制命令真能跑**：§1.1 的干净 env 配方 exit 0、12/13/14/15 复现；§5.1 的 289 点生成器
   产出的 payload 逐条满足 `RainfallScenarioRequest` 的全部校验。
6. **观测边界写得很干净**：`operations.md:270-275`、`:1194-1197`、`CONTRACT_INDEX.md:287-289`
   三处都声明那台栈"不是本模块起的、没起 production profile、不构成 live G6/AC-8"；
   本审查 §1.6 复核该栈确实没有 nginx/frontend 容器。

## 4. 下一轮建议的最小动作

1. 按 MEDIUM-1～4 逐条改 4 个 `path:line`（`operations.md:157`、`CONTRACT_INDEX.md:152`、`:14`、`:10-11`），
   改完重跑 `python -B .runtime/doc_checks.py`，确认 `CITATIONS_OUT_OF_RANGE=none` 且退出码仍为 0。
2. 统一容器计数口径（MEDIUM-5），并把 `README.md:976` 的过期根因与 `--env-file .env` 一并修掉（MEDIUM-6）。
3. 补 MEDIUM-7 的机制更正与 MEDIUM-8 的前提限定——这两条会让"干净环境"这一整套叙述真正自洽。
4. MEDIUM-9 的脚本侧修复归 scripts owner；文档侧至少加一句换行提示，
   避免下一轮再有人把 sidecar 哈希当成"可校验的 artifact hash"引用。
