# 水力业务 API 独立审查

- 模块：`hydraulic-api`
- 审查日期：2026-09-25
- 结论：**NOT_APPROVED / 不批准**
- 原因：发现 7 个 High、3 个 Medium。实现可生成 Swagger 路径且聚焦测试通过，但核心状态、任务、结果与 forecast 仍是进程内演示适配器，没有满足冻结的 PostgreSQL/Timescale durable contract；另有可复现的 RBAC/状态绕过、JWT 声明缺失、rainfall 数据完整性、OpenAPI、SSE 和错误关联问题。

## 审查边界与规范来源

本轮只读复核了任务列出的实现与测试，并追踪其直接依赖；未修改生产代码或测试，只写本报告。

采用的规范优先级：

1. `docs/hydraulic-twin/SOURCE_PRD.md`
2. `docs/hydraulic-twin/PRD_SUPPLEMENT.md`（Frozen v3）
3. `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`

`docs/hydraulic-twin/contracts/*.md` 文件自身均标为 `SUPERSEDED / NON-NORMATIVE`，没有用其旧状态机或路径覆盖冻结主契约。项目扫描未发现独立 `docs/adr/`；本轮以上述冻结 PRD/实施契约作为 ADR/spec gate 输入。

## Findings

### H-01 — import/validate/seed 不是 durable network job，重启与多进程一致性失效

**问题**

所有业务状态保存在模块级 `HydraulicApiStore` 的 Python dict/list 中。import 在 API 请求内完成预检、解析、materialize、建 network/revision，然后立即把 job 标成 `completed`；validate 与 seed 也在 API 进程内计算并直接提交 revision。202 只剩响应外壳。

这违反冻结契约“API 只做认证/RBAC/元数据/流式 quarantine，然后写 `network_jobs` + idempotency record；parser/validator/seed 由带 lease/fencing 的 worker 执行”。进程重启会丢全部 network/rainfall/simulation/job/idempotency 数据；多 worker 各自看到不同事实，同一幂等键也无法由数据库唯一约束/事务串行化。

**证据**

- `backend/app/api/hydraulic_routes.py:264-299`：全局 store、dict/list 状态与 reset。
- `backend/app/api/hydraulic_routes.py:1314-1374`：import 在请求内 `_preflight_import`、materialize，并创建 `completed` job。
- `backend/app/api/hydraulic_routes.py:2398-2460`：validate 直接计算 report 并创建 `completed` job。
- `backend/app/api/hydraulic_routes.py:2611-2735`：seed 直接生成实体、创建 revision 和 `completed` job。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:271-276`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1506-1516,2105`：冻结的 durable API/worker 边界。
- 实现者也明确承认该 adapter 不是 PostgreSQL/Timescale repository，且 API 进程会 materialize：`docs/hydraulic-twin/implementation/hydraulic-api.md:88-90`。

**影响**

数据在单次测试进程外不具备生产可用性；网络重启/多副本部署会产生数据分叉，202 job、幂等、取消和 worker 恢复语义都无法成立。

**修复方向**

生产路由必须依赖 SQLAlchemy repository：同一事务写 `idempotency_records` 与 `network_jobs`/目标资源并提交后返回 202；上传流写入受控 quarantine 后只传服务端 source ref。独立 network-job worker 使用 `FOR UPDATE SKIP LOCKED`、lease、heartbeat、fencing、retry/terminal 状态机完成解析、验证、seed 和原子发布。进程内 adapter 只保留为显式 test double，不能作为默认 app 依赖。

### H-02 — simulation/result/calibration/forecast 是不可达或硬编码的演示数据，不是已发布结果

**问题**

simulation submit 只向内存 dict 写一个 `queued` 行；仓库内没有消费者把它推进到 `completed`。唯一 frame 构造器根据 frame index 生成周期数值并固定标记 `quality.status="verified"`；A/B compare 固定返回 RMSE/NSE 常量。calibration submit 永远停在内存 `queued`。forecast store 没有任何生产写入口，所以 public workflow 下 latest/risk 永远 `not_ready`、transition 永远 404。

这不是 solver/worker 结果 API；即使测试或未来代码直接把内存状态改成 completed，也会向客户端发送伪造的 verified frame 和指标。

**证据**

- `backend/app/api/hydraulic_routes.py:3030-3141`：仅创建内存 simulation；`solver_version` 是 `"python-fallback-v1"`/`"epa-swmm-probed"` 字符串，未冻结真实 engine/version/hash。
- `backend/app/api/hydraulic_routes.py:3259-3320`：frame 值由 index 公式生成，并固定返回 verified/0.00001/0.4。
- `backend/app/api/hydraulic_routes.py:3391-3474`：A/B metrics 固定为 baseline/candidate `1.0/0.8` 与 `0.5/0.6`。
- `backend/app/api/hydraulic_routes.py:3506-3571`：calibration 仅写内存 queued 行。
- `backend/app/api/hydraulic_routes.py:3601-3619,3622-3697`：forecast 只读取从未由生产路径填充的内存 run/alert。
- `backend/tests/api/test_hydraulic_api.py:234-313`：测试只断言 queued/cancel/not_ready，没有结果、frame、calibration、A/B 或 forecast transition 成功路径。

**影响**

FR-3/FR-4/AC-3/AC-4/AC-5/AC-6 的 API 事实源不可用；调用方无法区分真实 published attempt 与演示数值，质量门禁和 forecast policy 也没有 API 执行点。

**修复方向**

移除 `_frame_payload` 和 compare 常量。submit/cancel/result/frame/calibration 必须只读 PostgreSQL/Timescale 的 durable resource 与 published attempt；worker/finalizer 负责 frame registration、summary/quality 和原子 publish。forecast 从 committed nowcast/alert/outbox repository 读取，并验证成功状态、freshness、target/manifest ownership 与版本化工件；没有事实时保持 `not_ready`，绝不合成 verified 结果。

### H-03 — operator 可通过 PATCH 绕过 admin-only archive，archived network 仍可提交 simulation

**问题**

`NetworkPatchRequest` 允许修改 `status`，PATCH 依赖是 `_operator`；代码把 `archived` 当普通字段写入。真正 DELETE archive 才要求 admin。与此同时 simulation submit 只校验 network/revision/rainfall 归属，不检查 network status，所以 archived network 仍返回 202 queued。

**证据**

- `backend/app/api/hydraulic_routes.py:319-323`：PATCH schema 接受 `status`。
- `backend/app/api/hydraulic_routes.py:1385-1422`：PATCH 使用 operator 并写入所有请求字段。
- `backend/app/api/hydraulic_routes.py:1425-1433`：DELETE archive 才使用 `_admin`。
- `backend/app/api/hydraulic_routes.py:3036-3051`：simulation submit 无 archived guard。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1475-1486,1504`：archive 是 admin 能力；`docs/hydraulic-twin/PRD_SUPPLEMENT.md:142,283` 明确 archive 后阻止新 simulation/nowcast 和 mutation。

定向 ASGI 诊断输出：

```text
operator_patch_archive_status= 200
operator_patch_archive_state= archived
submit_archived_network_status= 202
submit_archived_network_state= queued
```

**影响**

operator 可执行合同限定为 admin 的状态变更，并可继续给归档网络创建任务；RBAC 与 lifecycle 两个边界同时失效。

**修复方向**

从普通 PATCH DTO 删除 `status`，或至少拒绝 `status="archived"`；archive 只允许 DELETE/admin service path，并在同一事务设置状态。simulation、rainfall mutation、revision mutation 与 nowcast 入口统一调用 archived guard；并发检查必须与状态变更使用行锁/CAS。

### H-04 — rainfall PATCH 可写入负值、错误采样数和伪造 content hash

**问题**

create 模型会检查步长、采样数、递增时间与有限非负强度；但 PATCH 模型把 `points` 声明成裸 `list[dict]`，`content_sha256` 也是可选字符串。patch 直接覆盖新版本的 points/hash，没有重跑完整 scenario 校验，也不重算内容 hash。

**证据**

- `backend/app/api/hydraulic_routes.py:386-443`：create 才有完整 rainfall invariants。
- `backend/app/api/hydraulic_routes.py:447-450`：PATCH 无 points/hash 验证器。
- `backend/app/api/hydraulic_routes.py:2844-2890`：直接复制并覆盖 points/hash。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:295-300`：要求非负、有限、严格递增、总时长与可复现 hash；`IMPLEMENTATION_CONTRACT.md:1561-1570` 要求不可变 scenario/version。

定向 ASGI 诊断把一个 300 s scenario 改成单个 `-999.0` 点，仍返回 200：

```text
patch_status= 200
new_version_points= 1
new_first_intensity= -999.0
```

**影响**

可构造无法 materialize、无法重跑或带负降雨的“合法”scenario；后续 simulation 会继承破坏的数据，破坏 hash 可追溯性。

**修复方向**

PATCH 先在内存中构造候选完整 scenario，再复用同一个 closed Pydantic/domain validator 校验所有 invariants；content hash 必须由服务端对 canonical scenario 计算并与提交值核对，不能盲信客户端。新版本应与旧版本建立明确 parent/version lineage，且任何被 simulation 引用的根/版本都不可变。

### H-05 — JWT 未强制 `nbf` 和 access-token `type`，refresh/无类型 token 可访问水力 API

**问题**

水力认证依赖 `decode_access_token`，但 decoder 只要求 `exp/iat/sub`，没有要求 `nbf`，返回 payload 后也没有验证 `type == "access"`。因此一个签名有效但缺 `nbf/type` 的 token，甚至 `type="refresh"` 的 token，都能通过水力 viewer API。

**证据**

- `backend/app/core/security.py:124-149`：required claims 只有 exp/iat/sub；没有 `require_nbf` 或 access type 检查。
- `backend/app/api/hydraulic_routes.py:110-123`：只检查 decode 成功与 role 枚举。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1491,1747-1752`：认证 endpoint 必须校验 `exp/nbf/iat/sub/iss/aud/type/role`。

定向 ASGI 诊断输出：

```text
missing_type_and_nbf_status= 200
refresh_type_status= 200
```

**影响**

违反 token audience/type 边界；未来若同一 issuer 发行 refresh/impersonation token，它们会被水力 API 误当作 access token。

**修复方向**

在统一 decoder 中要求并验证 `nbf`、`type=access`，并保留 locked algorithms、iss/aud、exp/iat/sub；添加缺 nbf、错误 type、未来 nbf、错误 iss/aud/role 的负向 API 测试。安全修复应位于共享认证边界，hydraulic 路由只消费已验证 principal。

### H-06 — forecast 安全门禁与五类独立 rate-limit bucket 只有配置，没有执行

**问题**

配置声明了 forecast read/transition、simulation submit、network-job submit、SSE connect 五个限流桶，但应用代码没有任何读取这些字段或返回 `429 + Retry-After` 的路径。forecast router 也在所有环境无条件 include，没有 production G2 artifact/readiness gate。

**证据**

- `backend/app/core/config.py:372-384`：仅定义五个限额。
- 本轮执行 `rg -n "RATE_LIMIT_(FORECAST|SIMULATION|NETWORK_JOB|SSE)|Retry-After|rate.limit" backend/app`，输出只命中 `config.py:380-384`，没有 middleware/dependency/handler 使用点。
- `backend/app/api/routes.py:30,1092-1095` 与 `backend/app/main.py:268-269`：hydraulic/forecast 路由无条件注册。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1491-1492,1743-1754`：独立限流和 production G2 前置门禁为 MUST。

**影响**

攻击者可无限制触发 import/simulation/SSE/forecast 读写；即使 JWT/artifact 门禁尚未 PASS，production 仍公开 forecast endpoints，违反冻结发布顺序。

**修复方向**

实现按 principal+route class 的独立 limiter，429 必须带 `Retry-After`；将 forecast 注册置于可验证的 production capability gate 后，而非仅依赖 settings 布尔值。补 burst、跨 bucket 独立性、SSE connect、viewer write、错误 iss/aud/type/role 的 G2 测试。

### H-07 — Swagger 只证明“有路径”，55/57 个 JSON 成功响应是无字段 object

**问题**

几乎所有 hydraulic route 都使用 `response_model=dict[str, Any]`。OpenAPI 可以生成，但 response schema 只是 `type=object, additionalProperties=true`，客户端无法发现字段、必填项、枚举或错误 shape，不满足“所有 JSON 使用强类型 request/response model”和“Swagger 完整”。

**证据**

- 代表性路由：`backend/app/api/hydraulic_routes.py:1277-1311,3030-3141,3197-3219,3622-3657` 均声明 `dict[str, Any]`。
- `backend/tests/api/test_hydraulic_api.py:315-331`：OpenAPI 测试只断言 8 个 path 存在。
- 精确诊断输出：

```text
hydraulic_operations= 59
json_success_responses= 57
untyped_object_success_responses= 55
```

代表 schema：

```text
POST /api/networks 201 {'type': 'object', 'additionalProperties': True, 'title': 'Response Create Network Api Networks Post'}
GET /api/simulations/{simulation_id}/result 200 {'type': 'object', 'additionalProperties': True, 'title': 'Response Get Simulation Result Api Simulations  Simulation Id  Result Get'}
GET /api/forecast/latest 200 {'type': 'object', 'additionalProperties': True, 'title': 'Response Forecast Latest Api Forecast Latest Get'}
```

**影响**

OpenAPI 不能作为可生成客户端或冻结 wire contract 的证据，字段漂移只会在运行时暴露。

**修复方向**

为 Network/Revision/Entity/Job/Validation/Rainfall/Simulation/Frame/Result/Calibration/Forecast/SSE/error envelope 定义具体 Pydantic response models，并在 route 上引用；测试应递归断言关键 2xx response 为具名 `$ref` 且必填字段/枚举存在，而非只检查 path。

### M-01 — SSE 不是实时流，Last-Event-ID 重放会重复已消费事件

**问题**

SSE generator 先发一个非合同的 `forecast.ready`，遍历内存 event list，再发一次 heartbeat 后结束；没有持续订阅/队列/定时 heartbeat。重连时虽然解析了 cursor，却遍历全部事件而没有过滤 `sequence > cursor`，客户端会再次收到已处理事件。事件列表无 168h retention。

**证据**

- `backend/app/api/hydraulic_routes.py:3830-3876`：有限 generator、发送 ready、全量遍历、单次 heartbeat 后返回。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1863-1874`：要求 durable outbox、按 sequence replay、168h、gap/resync、每 15s heartbeat。

**影响**

普通浏览器连接会立即断开；重连不能正确增量恢复，跨 worker/restart 更无法工作，AC-4 的事件一次语义与 replay 不可达。

**修复方向**

从 committed `event_outbox`/Redis fan-out 建立每连接有界队列；握手时只 replay `sequence > Last-Event-ID` 且在保留窗内的事件，再无缝接 live；15s heartbeat、gap/过期 409、队列满关闭并携带 cursor 重连。用双 API worker + restart fixture 验证。

### M-02 — 未显式传 X-Request-ID 时，错误 body 与响应头关联到不同 UUID

**问题**

request-id middleware 和异常 handler 各自在请求无 header 时独立生成 UUID。handler 把自己的值写入 body，随后 middleware 用另一个值覆盖 header，客户端无法用同一 ID 检索日志。

**证据**

- `backend/app/main.py:256-261`：middleware 生成 request ID。
- `backend/app/main.py:325-327`：异常 handler 再次生成。
- 定向 ASGI 诊断：

```text
status= 401
header_request_id= 406ed536-f9a4-4ae9-91f4-bf996587845b
body_request_id= 4185449c-b9a5-4cc9-ad4a-7ce188793626
match= False
```

**影响**

错误 envelope 虽含 `request_id`，但无法与 `X-Request-ID` 一一关联，影响告警、审计和故障定位。

**修复方向**

middleware 只生成一次并写入 `request.state.request_id`；异常 handler、structured log 和 response header 都读取同一个值。增加“未传 header”与“显式传 header”两条契约测试。

### M-03 — map-data 无 limit/cursor/truncation，始终声明完整返回

**问题**

bbox 的 exact geometry filter 本身存在且聚焦测试通过；但 `map_data` 会把所有匹配 nodes/pipes/catchments 一次物化，没有冻结的 limit、cursor 或服务端预算，且无论数量多少都返回 `complete=true`、`truncated_layers=[]`。在 5000/10000/1000 目标规模上会制造大 JSON、内存和 P95 风险。

**证据**

- `backend/app/api/hydraulic_routes.py:1603-1642,2321-2395`：遍历完整 revision 集合后直接构造所有 FeatureCollection，返回 `complete=True`。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:417-427`：目标规模与大结果分页要求；`IMPLEMENTATION_CONTRACT.md:1276` 要求超预算返回 `complete=false` 与 `truncated_layers[]`。
- `backend/tests/api/test_hydraulic_api.py:93-105`：只用 2 nodes/1 pipe 验证 bbox，不覆盖规模/截断。

**影响**

bbox 过滤降低选择集但不限制输出，API 可能在验收规模一次返回全部 1.5 万对象并破坏延迟/内存目标。

**修复方向**

按 layer 设置冻结 limit、opaque cursor 与 `complete/truncated_layers`；LOD 简化只能改变 geometry，不能重排 manifest feature index。用 5000/10000/1000 fixture 验证计数、排序、cursor 稳定性、内存和 P95。

## 测试覆盖缺口

现有聚焦文件只有 5 个测试。它覆盖了基本 network/entity 创建、单次 bbox、两种上传拒绝、顺序幂等、rainfall create/copy、simulation submit/cancel、forecast not-ready 和 path presence，但未覆盖本审查发现的高风险边界：

- 完整恶意文件矩阵：zip/magic、截断、脚本、深层/NaN、重复 stable key、路径变体、清理与前后 readiness；
- 同 key 不同 hash、同 key 并发、24h generation、进程重启/多 worker；
- 所有 mutation 的 viewer/operator/admin 角色矩阵、跨用户/network ownership、operator archive 绕过；
- archived network 的 rainfall/revision/simulation/nowcast 全拒绝；
- rainfall PATCH 完整 invariants 与 canonical hash；
- simulation dry/warm initial state、solver capability、published attempt/frame retention 410、cancel race；
- result/A-B/calibration/forecast ready/stale/transition/SSE replay；
- OpenAPI 具名 schema/错误 response/Idempotency header/security requirement。

因此“5 passed”只证明当前五个浅用例通过，不能作为模块完成或 G2 通过证据。

## 本轮实际执行

### 1. 任务指定的聚焦测试

精确命令：

```bash
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && .venv/Scripts/pytest.exe -q tests/api/test_hydraulic_api.py
```

结果摘要：

```text
.....                                                                    [100%]
5 passed, 524 warnings in 0.33s
```

524 条 warning 均为 FastAPI/Starlette/pytest-asyncio/python-jose 的 deprecation warning；本轮未把它们计作通过或失败门禁。

### 2. Archive/状态绕过 ASGI 诊断

精确命令：

```bash
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && .venv/Scripts/python.exe - <<'PY'
import asyncio, uuid
import httpx
from app.main import app
from app.api.hydraulic_routes import reset_hydraulic_api_store
from app.core.security import create_access_token

async def main():
    reset_hydraulic_api_store()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as c:
        def h(role, key):
            return {'Authorization': 'Bearer ' + create_access_token(str(uuid.uuid4()), extra_claims={'role': role}), 'Idempotency-Key': key}
        n = (await c.post('/api/networks', json={'code':'ARCHIVE-BYPASS','name':'n'}, headers=h('operator','create-network-1'))).json()
        rev = n['current_revision_id']
        rain = await c.post('/api/rainfall-scenarios', json={
            'network_id':n['id'],'name':'r','kind':'custom','return_period_years':None,
            'step_seconds':300,'duration_seconds':300,'source_type':'synthetic',
            'algorithm':'synthetic-custom-v1','random_seed':1,'content_sha256':'c'*64,
            'points':[{'point_index':0,'valid_at':'2026-09-25T00:00:00Z','intensity_mm_h':0.0},{'point_index':1,'valid_at':'2026-09-25T00:05:00Z','intensity_mm_h':1.0}]
        }, headers=h('operator','create-rain-1'))
        patched = await c.patch(f"/api/networks/{n['id']}", params={'expected_revision_id':rev}, json={'status':'archived'}, headers=h('operator','archive-patch-1'))
        rev2 = patched.json().get('current_revision_id')
        sim = await c.post('/api/simulations', json={
            'network_id':n['id'],'network_revision_id':rev2,'rainfall_scenario_id':rain.json()['id'],
            'parameter_set_id':str(uuid.uuid4()),'initial_state_id':None,'solver_selection':'auto',
            'start_at':'2026-09-25T00:00:00Z','duration_seconds':300,'frame_interval_seconds':300,
            'ponding_enabled':False,'strict_stability':True
        }, headers=h('operator','submit-archived-1'))
        print('operator_patch_archive_status=', patched.status_code)
        print('operator_patch_archive_state=', patched.json().get('status'))
        print('submit_archived_network_status=', sim.status_code)
        print('submit_archived_network_state=', sim.json().get('status'))

asyncio.run(main())
PY
```

输出即 H-03 所列四项。

### 3. OpenAPI response schema 诊断

精确命令：

```bash
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && .venv/Scripts/python.exe - <<'PY'
from app.main import app
spec=app.openapi()
ops=[]
for path,item in spec['paths'].items():
    for method,op in item.items():
        if method in {'get','post','put','patch','delete'} and 'hydraulic' in op.get('tags',[]):
            ops.append((method,path,op))
success=generic=0
for _,_,op in ops:
    for status,response in op['responses'].items():
        if status.startswith('2') and response.get('content'):
            success += 1
            schema=response['content']['application/json']['schema']
            generic += schema.get('type')=='object' and schema.get('additionalProperties') is True
print('hydraulic_operations=',len(ops))
print('json_success_responses=',success)
print('untyped_object_success_responses=',generic)
PY
```

输出：

```text
hydraulic_operations= 59
json_success_responses= 57
untyped_object_success_responses= 55
```

### 4. Rainfall PATCH 诊断

精确命令：

```bash
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && .venv/Scripts/python.exe - <<'PY'
import asyncio, uuid, httpx
from app.main import app
from app.api.hydraulic_routes import reset_hydraulic_api_store
from app.core.security import create_access_token
async def main():
    reset_hydraulic_api_store()
    def h(key): return {'Authorization':'Bearer '+create_access_token(str(uuid.uuid4()),extra_claims={'role':'operator'}),'Idempotency-Key':key}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        n=(await c.post('/api/networks',json={'code':'RAIN-PATCH','name':'n'},headers=h('network-create-1'))).json()
        body={'network_id':n['id'],'name':'r','kind':'custom','return_period_years':None,'step_seconds':300,'duration_seconds':300,'source_type':'synthetic','algorithm':'synthetic-custom-v1','random_seed':1,'content_sha256':'d'*64,'points':[{'point_index':0,'valid_at':'2026-09-25T00:00:00Z','intensity_mm_h':0.0},{'point_index':1,'valid_at':'2026-09-25T00:05:00Z','intensity_mm_h':1.0}]}
        s=(await c.post('/api/rainfall-scenarios',json=body,headers=h('rain-create-1'))).json()
        r=await c.patch(f"/api/rainfall-scenarios/{s['id']}",json={'expected_version':1,'points':[{'point_index':0,'valid_at':'2026-09-25T00:00:00Z','intensity_mm_h':-999.0}]},headers=h('rain-patch-1'))
        print('patch_status=',r.status_code)
        print('new_version_points=',len(r.json().get('points',[])))
        print('new_first_intensity=',r.json().get('points',[{}])[0].get('intensity_mm_h'))
asyncio.run(main())
PY
```

输出：

```text
patch_status= 200
new_version_points= 1
new_first_intensity= -999.0
```

### 5. JWT claim 诊断

精确命令：

```bash
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && .venv/Scripts/python.exe - <<'PY'
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
from jose import jwt
from app.main import app
from app.core.config import settings

def token(**extra):
    now=datetime.now(timezone.utc)
    claims={'sub':'00000000-0000-0000-0000-000000000001','role':'viewer','iat':now,'exp':now+timedelta(minutes=5)}
    claims.update(extra)
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm='HS256', headers={'alg':'HS256'})

async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as c:
        missing=await c.get('/api/networks',headers={'Authorization':'Bearer '+token()})
        wrong_type=await c.get('/api/networks',headers={'Authorization':'Bearer '+token(type='refresh')})
        print('missing_type_and_nbf_status=',missing.status_code)
        print('refresh_type_status=',wrong_type.status_code)
asyncio.run(main())
PY
```

输出：

```text
missing_type_and_nbf_status= 200
refresh_type_status= 200
```

### 6. Request ID 诊断

精确命令：

```bash
cd '/c/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' && .venv/Scripts/python.exe - <<'PY'
import asyncio, httpx
from app.main import app
async def main():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        r=await c.get('/api/networks')
        print('status=',r.status_code)
        print('header_request_id=',r.headers.get('X-Request-ID'))
        print('body_request_id=',r.json().get('request_id'))
        print('match=',r.headers.get('X-Request-ID') == r.json().get('request_id'))
asyncio.run(main())
PY
```

输出即 M-02 所列四项，`match=False`。

## 明确未运行

按任务要求，本轮没有重复最终全量门禁。以下均 **NOT RUN**，不能记作通过：

- backend 全量 pytest、Black/isort/Flake8/mypy；
- G0～G6、AC-1～AC-8 与 `scripts/verify_hydraulic_stack.py`；
- live PostgreSQL/PostGIS/Timescale migration/约束/并发；
- 真实 network/simulation/calibration/nowcast worker、solver、Redis outbox/SSE；
- 双 API worker、restart/replay、rate-limit burst；
- Docker/Compose、5000/10000 API benchmark、浏览器 timeline；
- 恶意文件完整矩阵与目录清理探针。

## 最终判定

**NOT_APPROVED。** 聚焦测试通过只能证明现有 5 个用例与进程内 adapter 自洽，不能证明 PRD 水力业务 API 已实现。至少 H-01～H-07 关闭并补齐相应负向/并发/重启/真实数据测试后，才应重新提交独立审查。
