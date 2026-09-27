# Forecast SSE 与通知模块审查

- 审查对象：`forecast-events`
- 审查日期：2026-09-25
- 结论：**不批准（存在 5 个 High、1 个 Medium）**
- 审查方式：只读检查冻结契约、当前工作树真实实现、调用点与目标测试；未修改生产代码或测试。

## 1. 冻结契约与审查基线

本审查以以下冻结要求为准：

- canonical SSE endpoint 为 `GET /api/forecast/events?network_id=<uuid>`，replay 按持久化 `sequence`，有效范围 168 h，过期 cursor 在连接前返回 409；生产 API 只消费 Redis 并 fan-out：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1816-1874`。
- `hydraulic.event_outbox.sequence BIGSERIAL` 是 SSE `Last-Event-ID`；`hydraulic.notification_deliveries` 记录逐目标状态、lease、retry 与 dead letter：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1184-1223`。
- Webhook 必须使用管理员 HTTPS allowlist，并校验 DNS/IP/redirect/response size，带 event idempotency header 与 HMAC：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1889-1897`、`docs/hydraulic-twin/PRD_SUPPLEMENT.md:401`。
- 未知 `schema_version` 或缺字段不得进入客户端 store，应记录指标并触发 resync：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1872-1874`。

仓库没有 `docs/adr/` 或 `_project_specs/`；当前工作树也无 `.codegraph/` 索引，因此本次使用真实文件读取与精确 `rg` 调用点核对。

## 2. 阻断发现

### H-01 — canonical `/forecast/events` 绕过新 SSE 管理器，重连会重放旧事件并立即结束连接

**问题**

真实路由没有调用 `connect_forecast()` / `forecast_event_generator()`，而是遍历进程内 `hydraulic_store.events` 后结束生成器。它只解析 cursor，却完全不按 cursor 过滤事件；还额外发送契约中不存在的 `forecast.ready` 与 `forecast.heartbeat` 业务帧。新实现因此没有服务 canonical endpoint，客户端会收到重复旧事件、非法帧，并在快照发送完后断线重连。

**证据**

- 冻结 endpoint、wire format 与 replay 语义：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1816-1874`。
- 真实路由使用 `len(hydraulic_store.events)` 和内存列表，不使用 `SSEManager`：`backend/app/api/hydraulic_routes.py:3026-3051`。
- 路由对 `Last-Event-ID=1` 的实际诊断输出：

```text
[('1', 'forecast.ready', None), ('1', 'alert.created', 'event-1'), (None, 'forecast.heartbeat', None)]
```

执行命令：

```bash
PYTHONPATH="C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/.venv/Scripts/python.exe" - <<'PY'
import asyncio, json
from types import SimpleNamespace
from app.api.hydraulic_routes import forecast_events, hydraulic_store
NETWORK = "11111111-1111-4111-8111-111111111111"
hydraulic_store.events = [{
    "schema_version": 1, "event_id": "event-1", "sequence": 1,
    "event_type": "alert.created", "occurred_at": "2026-09-25T08:00:00Z",
    "network_id": NETWORK, "nowcast_run_id": "run-1", "simulation_id": "sim-1",
    "alert": {"id": "alert-1"},
}]
async def main():
    response = await forecast_events(
        SimpleNamespace(headers={"Last-Event-ID": "1"}), network_id=NETWORK, _={}
    )
    frames = []
    async for frame in response.body_iterator:
        payload = json.loads(frame["data"])
        frames.append((frame.get("id"), payload.get("event_type"), payload.get("event_id")))
    print(frames)
asyncio.run(main())
PY
```

- 前端 parser 要求 SSE `id`、完整 envelope 和合法事件类型；`forecast.ready`/`forecast.heartbeat` 会被判为非法：`frontend/src/features/simulation/forecast/stream.ts:69-127`。
- 当前目标测试只直接实例化 `SSEManager`，没有调用 `/forecast/events`：`backend/tests/hydraulic/test_forecast_events.py:54-164`。

**影响**

Forecast SSE 的主要用户路径不可用；Last-Event-ID 单测通过不能证明真实 endpoint 可重连，且会制造重复业务效果。

**修复要求**

让 canonical route 调用 `connect_forecast(last_event_id, network_id=...)` 与 `forecast_event_generator(...)`，将 `SSECursorExpiredError` 映射为 409；只发送契约事件并保持长连接。增加真实认证路由测试，断言 cursor 等于已收 sequence 时不重放、cursor 后事件按序到达、连接持续等待 live event、过期/超前 cursor 为 409。

### H-02 — forecast Redis、outbox relay 与通知投递均未接入生产调用链

**问题**

新增方法目前是定义即止：应用 lifespan 只注册 legacy alerts/sensor/model 三个 Redis channel；没有 forecast subscriber、outbox publisher、outbox 表/relay，也没有生产代码调用 `notify_forecast_event()`。因此不会产生实时 forecast 事件，也不会产生外部通知。

**证据**

- lifespan 仅注册三个 legacy channel 后启动 listener：`backend/app/main.py:147-153`。
- 生产调用点精确搜索：

```bash
rg -n --glob '!nul' "on_forecast\(|publish_forecast_event\(|broadcast_forecast_event\(|connect_forecast\(" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/app"
```

输出仅有定义：

```text
backend/app/core/redis_client.py:125:    async def publish_forecast_event(self, event: Mapping[str, Any]) -> int:
backend/app/core/redis_client.py:171:    def on_forecast(self, handler: MessageHandler) -> None:
backend/app/services/sse_manager.py:144:    async def connect_forecast(
backend/app/services/sse_manager.py:226:    async def broadcast_forecast_event(
```

```bash
rg -n --glob '!nul' "notify_forecast_event\(" "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/app"
```

输出：

```text
backend/app/services/notification_service.py:280:    async def notify_forecast_event(
```

- ORM/migration 中搜索 `event_outbox|notification_deliveries` 无输出；当前模型从 forecast transition 直接进入 retention policy：`backend/app/models/hydraulic_models.py:2633-2683`。
- 冻结数据模型要求两张持久表：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1184-1223`。
- 实施记录也明确承认 route/lifespan、delivery state machine 与 worker 尚未接入：`docs/hydraulic-twin/implementation/forecast-events.md:121-127`。

**影响**

本模块的 service 单元行为即使正确，也不会在运行应用中出现；objective 中的“复用告警持久化、Redis/SSE、notification_service”与“避免重复广播”尚未形成端到端保证。

**修复要求**

由集成/worker owner 完成 durable outbox relay、`redis_client.on_forecast(...)` 注册、逐事件 `published_at` 标记及 notification delivery worker；在集成证据提交前不应把本模块标为完成。保持 producer 只写 outbox、relay 单发布、API 只消费 Redis，禁止 direct broadcast + Redis echo。

### H-03 — replay 仍是进程内“最近 1000 条”，不满足 168 h、跨 worker 与重启恢复

**问题**

`SSEManager` 用固定条数裁剪历史，没有时间维度，也不从 `hydraulic.event_outbox` 读取 replay。API 重启后 history/high-water mark 清空；多 worker 各自看到不同 history；高频事件可让 168 h 内的 cursor 提前 409，而低频时过期 cursor 又可能继续被进程内缓存接受。

**证据**

- 固定 `_MAX_HISTORY = 1000`：`backend/app/services/sse_manager.py:72-75`。
- forecast history 与 latest sequence 仅为进程内字段：`backend/app/services/sse_manager.py:89-108`。
- 裁剪仅按列表长度：`backend/app/services/sse_manager.py:302-317`。
- replay/cursor 边界只读取该内存列表：`backend/app/services/sse_manager.py:466-516`。
- 契约要求 DB `BIGSERIAL` sequence、168 h replay：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1184-1204`、`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1865-1870`。
- 测试仅验证同一 manager 实例的 4 条/1 条缓存边界：`backend/tests/hydraulic/test_forecast_events.py:77-110`；没有重启、多 worker 或 168 h 测试。
- 实施记录承认不是跨 worker/重启实现：`docs/hydraulic-twin/implementation/forecast-events.md:121-124`。

**影响**

Last-Event-ID 在生产多 worker/重启场景下不确定，可能产生不完整 replay、错误 409 或重复/缺口处理依赖客户端偶然行为。

**修复要求**

连接前从 `hydraulic.event_outbox` 按 `sequence > cursor AND expires_at > now()` 做持久 replay，并用持久 earliest/latest 校验 cursor；内存 history 只能作为优化，不能作为事实源。增加 168 h 边界、进程重启和两个 manager/worker 的集成测试。

### H-04 — forecast webhook 安全边界未满足冻结契约

**问题**

URL 校验只拒绝 literal 私网 IP；任意普通 hostname 直接放行，不能阻止 DNS 解析到私网、loopback 或 DNS rebinding。HTTP 响应被 `httpx.post()` 整体读取且没有大小上限；3xx 因 `status_code < 400` 被记为成功；HMAC 只在调用方显式传 secret 时存在，而 `notify_forecast_event()` 默认允许不传 secret。

**证据**

- 冻结要求 DNS/IP/redirect/response size 与 HMAC：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1889-1897`、`docs/hydraulic-twin/PRD_SUPPLEMENT.md:401`。
- HMAC 为可选分支，3xx 被当成功，响应未限流：`backend/app/services/notification_service.py:111-160`。
- `notify_forecast_event()` 的 secret 默认值为 `None` 并原样传递：`backend/app/services/notification_service.py:279-284`、`backend/app/services/notification_service.py:343-355`。
- hostname 只要不是 literal IP 就直接返回 `True`：`backend/app/services/notification_service.py:516-539`。
- 实际诊断输出：

```text
{'hostname_accepted': True}
```

- 实施记录明确承认 DNS rebinding 防护未完成：`docs/hydraulic-twin/implementation/forecast-events.md:126-127`。

**影响**

管理员配置错误或被滥用的 webhook 可造成 SSRF；恶意/异常 endpoint 还可消耗内存。3xx 被误报 delivered，缺少 secret 时发送方无法验证消息真实性。

**修复要求**

使用受控 webhook sender：严格匹配管理员 allowlist，解析并校验全部 A/AAAA，阻止私网/loopback/link-local 与 rebinding；禁止 redirect 并把 3xx 视为失败；流式读取并限制响应字节；生产路径缺 secret 时 fail closed。补 DNS→私网、redirect、超大响应、缺 secret 的测试。

### H-05 — SSE ingress 接受未知 schema 与缺失必填字段的 envelope

**问题**

`broadcast_forecast_event()` 只严格校验 event_id、sequence、event_type；`schema_version` 被 `int(...)` 转换但不要求等于 1，`occurred_at/network_id/nowcast_run_id/simulation_id/alert` 等必填字段也不验证。未知 schema 或残缺 payload 会进入 history 并广播，违反“拒绝、记录指标、触发 resync”的冻结要求。

**证据**

- envelope 更新时仅强制写回 schema/event_id/sequence/event_type：`backend/app/services/sse_manager.py:255-288`。
- network filter 对缺字段仅让指定 network 客户端不匹配，wildcard 客户端仍会收到：`backend/app/services/sse_manager.py:319-327`。
- Redis publisher 同样只校验 event_id/type/sequence/network_id，不校验 schema 与嵌套 alert：`backend/app/core/redis_client.py:124-160`。
- 契约要求未知 schema/缺字段触发 resync：`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1872-1874`。
- 实际诊断输出：

```text
{'unknown_schema_missing_fields_accepted': True, 'queued_data': '{"event_id":"event-malformed","event_type":"alert.created","schema_version":999,"sequence":1}'}
```

**影响**

损坏的 outbox/Redis 消息会污染客户端协议，触发前端反复 invalid-event resync；同时服务端没有协议指标可用于发现坏 relay 数据。

**修复要求**

在 Redis ingress、history/dedupe 状态变更之前做严格 runtime schema 校验：`schema_version == 1`、必填字段/类型/UUID/时间/alert 结构均验证；失败时记录低基数指标并向相关客户端发送 resync 或关闭连接。增加缺字段、未知 schema、非有限数值和错误 network 的测试。

## 3. Medium 发现

### M-01 — resync sentinel 使用陈旧 cursor，且缺少契约要求的 `from_sequence/to_sequence`

**问题**

慢客户端 overflow 时，`client.last_event_id` 从未随 live 事件更新，因此 sentinel 内的 `last_event_id` 永远是连接时 cursor。payload 也没有 `from_sequence/to_sequence`，客户端无法知道准确的缺口范围。

**证据**

- live 投递只更新 `last_event_at/event_count`，不更新 `client.last_event_id`：`backend/app/services/sse_manager.py:329-337`。
- sentinel 写入固定 `client.last_event_id`：`backend/app/services/sse_manager.py:518-543`。
- 前端冻结契约要求 payload 含 `from_sequence/to_sequence`：`docs/hydraulic-twin/contracts/frontend.md:461-464`。
- 当前测试只断言 `event_type`，不检查范围字段：`backend/tests/hydraulic/test_forecast_events.py:150-164`。
- overflow 诊断中，连接 cursor 为 41、已发生 42/43/44 时，sentinel 仍为：

```text
{'event_id': 'resync-8c061ba9', 'event_type': 'forecast.resync_required', 'last_event_id': 41, 'network_id': 'net', 'reason': 'forecast_client_queue_full', 'schema_version': 1}
```

**影响**

客户端虽可能触发 REST resync，但得到的是陈旧边界，不能可靠记录或恢复缺口；测试会放过协议漂移。

**修复要求**

维护每客户端最后成功入队/已发送 sequence，sentinel 输出准确的 `from_sequence/to_sequence`（或按冻结协议关闭连接），并对 payload 字段做精确断言。

## 4. 已执行检查

### 4.1 目标测试（按任务要求）

```bash
"C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/.venv/Scripts/python.exe" -m pytest "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/hydraulic/test_forecast_events.py" -q --tb=short
```

结果：`11 passed, 714 warnings in 0.06s`，exit 0。warning 来自当前 Python 3.14 下 FastAPI/pytest-asyncio 的 deprecation。

该结果只证明 isolated service 测试通过；没有覆盖真实 `/forecast/events` 路由、lifespan/Redis listener、outbox relay、notification worker、重启或多 worker。

### 4.2 真实路由、协议 ingress、webhook 与 resync 小型诊断

本轮还执行了上文逐项列出的四段 Python heredoc 诊断，分别证明：

1. canonical route 对 `Last-Event-ID=1` 仍重放 sequence 1，并发送非法 ready/heartbeat 后结束；
2. `schema_version=999` 且缺必填字段的 envelope 被接受并入队；
3. `https://127.0.0.1.nip.io` 通过 webhook hostname 校验；
4. overflow sentinel 使用陈旧 `last_event_id=41` 且无 from/to sequence。

### 4.3 未运行

按任务要求未重复最终全量门禁。以下均未在本轮运行，不能记为通过：完整 pytest、legacy service suite、Black/isort/flake8/mypy、live Redis/PostgreSQL、outbox/notification worker、168 h replay、多 worker/重启验证、官方 G4/AC-4。实施记录中列出的这些历史结果仅作为实现者材料引用，未由本审查重新验证。

## 5. 结论

目标单测为绿，但真实应用路径仍停留在未接入的 service 原型：canonical endpoint 行为错误，Redis/outbox/通知没有生产调用链，replay 不是持久 168 h，协议与 webhook 安全边界也未满足冻结契约。因此本模块当前 **approved=false**。
