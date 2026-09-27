# Forecast SSE 与通知实施记录

日期：2026-09-25  
模块：`forecast-events`

## 1. 契约与范围

本轮先读取了：

- `docs/hydraulic-twin/SOURCE_PRD.md`：FR-4/FR-5 要求 forecast 进入告警中心、SSE、通知，且事件需有预计时刻、提前量、深度、置信度和 simulation id（`SOURCE_PRD.md:22-24`）。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md`：冻结 v3 要求 outbox 单发布、Redis 只做扇出、`Last-Event-ID` 按 sequence replay、重复 `event_id` 不产生第二业务效果、通知失败不回滚 alert（`PRD_SUPPLEMENT.md:159-164,383-400`）。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md`：SSE wire format、提交/replay/背压和通知要求（`IMPLEMENTATION_CONTRACT.md:1816-1897`）。

本轮只修改任务授权的 3 个生产文件和 1 个测试文件；没有修改 API 路由、ORM/migration、worker 或配置。

## 2. 实际改动

### 2.1 Forecast SSE 与 Last-Event-ID

`backend/app/services/sse_manager.py`：

- 新增独立 forecast client 集合，legacy `connect()`/`broadcast()`/`event_generator()` 保持原 API，forecast 不会进入 `/api/sse/events` 的 legacy client 集合（`backend/app/services/sse_manager.py:144-170,321-348`）。
- 新增 `connect_forecast(last_event_id, network_id=...)`，在同一锁内完成 replay 快照和 live client 注册，避免快照与订阅之间的空窗（`backend/app/services/sse_manager.py:144-170`）。
- `broadcast_forecast_event()` 只消费持久 outbox/Redis 提供的 `sequence`，不在 API 进程重新生成业务 ID；同一 `event_id` 或已处理 sequence 返回 `False`，不向客户端重复投递（`backend/app/services/sse_manager.py:226-320`）。
- `forecast_event_generator()` 先按 `Last-Event-ID` 顺序输出 replay，再进入 live queue；SSE `id` 固定为 outbox `sequence`（`backend/app/services/sse_manager.py:414-465`）。
- 过期、超前或非法 cursor 抛出 `SSECursorExpiredError(code="SSE_CURSOR_EXPIRED")`；慢客户端 queue 满时写入 `forecast.resync_required` sentinel 并关闭连接，不静默跳过（`backend/app/services/sse_manager.py:39-52,467-517,519-547`）。
- forecast client 可按 `network_id` 隔离，且有独立 queue size/history limit；`client_count` 仍只表示 legacy client，保持既有健康检查语义。

### 2.2 Redis 单发布路径

`backend/app/core/redis_client.py`：

- 新增固定默认频道 `scn:forecast`，若未来配置提供 `REDIS_CHANNEL_FORECAST` 则优先使用配置（`backend/app/core/redis_client.py:25,100-105`）。
- `publish_forecast_event()` 校验 `event_id/event_type/sequence/network_id` 后只发布到 Redis，不直接调用本地 SSE，避免 producer direct broadcast + Redis echo 双发（`backend/app/core/redis_client.py:125-163`）。
- `on_forecast()` 提供 API fan-out 注册入口；调用方应在 `start_listener()` 前传入 `sse_manager.broadcast_forecast_event`（`backend/app/core/redis_client.py:171-179`）。
- Redis handler 继续逐 handler 隔离异常，`CancelledError` 可正确向上传播（`backend/app/core/redis_client.py:198-229`）。

集成调用顺序固定为：

```text
outbox relay -> redis_client.publish_forecast_event(outbox_row)
             -> scn:forecast
             -> sse_manager.broadcast_forecast_event
             -> forecast SSE clients
```

### 2.3 Forecast 通知模板与失败隔离

`backend/app/services/notification_service.py`：

- 保留 legacy `send_email/send_webhook/notify_alert` 调用方式；SMTP 同步 I/O 改由 `asyncio.to_thread` 执行，不阻塞 event loop（`backend/app/services/notification_service.py:45-110`）。
- 新增 forecast plain-text/HTML/webhook 模板；所有动态 HTML 值使用 `html.escape(..., quote=True)`，邮件 subject 去除 CR/LF，recipient 拒绝换行（`backend/app/services/notification_service.py:164-278,509-517`）。
- `notify_forecast_event()` 默认只为 `warning/critical` 的 `alert.created/alert.escalated/alert.observed` 发送外部通知；`alert.resolved` 仅 critical；`alert.updated` 不重复通知（`backend/app/services/notification_service.py:280-375`）。
- 每个邮件/webhook destination 独立捕获失败并返回 attempted/succeeded/failed 计数；单个 provider 失败不会阻断其他目标或回滚 alert（`backend/app/services/notification_service.py:377-421`）。
- Webhook 携带 `X-Forecast-Event-Id` 与 `Idempotency-Key`；调用方提供 secret 时按 canonical JSON 计算 HMAC-SHA256，并禁止 redirect（`backend/app/services/notification_service.py:112-163`）。
- Forecast webhook 仅接受管理员现有 `ALERT_WEBHOOK_URLS` 中的 HTTPS URL，并拒绝 literal 私网/loopback/link-local 地址（`backend/app/services/notification_service.py:515-539`）。

## 3. 新增测试

新增 `backend/tests/hydraulic/test_forecast_events.py`，覆盖：

- 持久 sequence 作为 SSE id，同 event/sequence 单次投递：`:56`。
- `Last-Event-ID` replay 与超前 cursor：`:79`。
- history 裁剪后的过期 cursor：`:101`。
- legacy/forecast stream 隔离和 network 隔离：`:115,132`。
- 慢客户端 queue overflow 后 resync + disconnect：`:152`。
- Redis publisher 不做本地 direct broadcast：`:178`。
- 通知 HTML 全量转义：`:200`。
- 单目标失败隔离：`:223`。
- `alert.updated` 不产生重复通知：`:277`。
- webhook event id/idempotency header 与 HMAC：`:299`。

## 4. 实际执行的检查

### 4.1 Forecast 目标测试

```bash
cd backend
./.venv/Scripts/python.exe -m pytest tests/hydraulic/test_forecast_events.py -q --tb=short
```

结果：`11 passed, 717 warnings in 0.06s`，exit 0。warning 来自当前 Python 3.14/pytest-asyncio/fastapi 的 deprecation，不是断言失败。

### 4.2 Forecast + legacy SSE 回归

```bash
cd backend
./.venv/Scripts/python.exe -m pytest tests/hydraulic/test_forecast_events.py tests/test_services/test_sse_manager.py -v --tb=short
```

结果：`14 passed, 852 warnings in 0.08s`，exit 0。

### 4.3 Legacy service 回归

```bash
cd backend
./.venv/Scripts/python.exe -m pytest tests/test_services -v --tb=short
```

结果：`6 passed, 494 warnings in 0.04s`，exit 0。

### 4.4 格式、import、lint、局部类型检查

```bash
cd backend
./.venv/Scripts/python.exe -m black --check app/services/sse_manager.py app/core/redis_client.py app/services/notification_service.py tests/hydraulic/test_forecast_events.py
./.venv/Scripts/python.exe -m isort --check-only app/services/sse_manager.py app/core/redis_client.py app/services/notification_service.py tests/hydraulic/test_forecast_events.py
./.venv/Scripts/python.exe -m flake8 app/services/sse_manager.py app/core/redis_client.py app/services/notification_service.py tests/hydraulic/test_forecast_events.py
./.venv/Scripts/python.exe -m mypy --follow-imports=skip app/services/sse_manager.py app/core/redis_client.py app/services/notification_service.py
```

结果：Black 报 `4 files would be left unchanged`；isort/flake8 无输出且 exit 0；mypy 报 `Success: no issues found in 3 source files`，整体 exit 0。

正常导入图的 mypy 命令也已执行：

```bash
./.venv/Scripts/python.exe -m mypy app/services/sse_manager.py app/core/redis_client.py app/services/notification_service.py
```

结果：本轮 3 个 owned source 无错误，但命令因未修改的 `app/core/config.py:210,489` 两处既有类型错误 exit 1；因此不能把正常导入图 mypy 记为全绿。

## 5. 风险与未验证项

1. **不是跨 worker/重启的持久 replay 实现。** 本模块使用 outbox 提供的 sequence，并在进程内保留有界 replay cache；冻结契约要求的 168h/跨 worker replay 必须由 integration 层从 `hydraulic.event_outbox` 读取。当前 owned paths 不含 repository/route/worker，未越界修改。
2. **尚未接入应用 lifespan/route。** 集成层需在 listener 启动前调用 `redis_client.on_forecast(sse_manager.broadcast_forecast_event)`，forecast route 调用 `connect_forecast()` + `forecast_event_generator()`，并将 `SSECursorExpiredError` 映射为 409。
3. **通知 delivery 状态机不在本轮。** 本模块返回投递计数并隔离 provider 失败，但 `notification_deliveries` 的 claim/retry/DLQ/lease 需要 outbox delivery worker 持久化。
4. **Webhook secret 需由集成层注入。** 代码支持 HMAC，但本轮不能修改 config，未增加 secret 字段；未提供 secret 时不会伪造签名。
5. **DNS rebinding 防护未完成。** 当前只做 HTTPS、禁止 credentials、拒绝 literal 私网/loopback/link-local；解析后 IP 校验与连接绑定应由受控 webhook sender 完成。
6. **运行环境不是契约 Python 3.11。** 可用项目 venv 实际为 Python 3.14.4；系统有 CPython 3.11.15，但没有安装 pytest/black，因此本轮测试未在 3.11 执行。
7. **未运行 live Redis/PostgreSQL/worker、全量 pytest 或官方 G4/AC-4。** 当前仓库还有大量并行未提交改动；本轮只证明 owned service 的单元行为和 legacy service 零回归。
8. **未提交、未推送。**
