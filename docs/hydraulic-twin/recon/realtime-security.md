# 实时告警与安全现状侦察

> 侦察对象：`smart-city-drainage` 当前工作树；基线为本次 `git log -1 --oneline` 输出的 `ca99e04 chore: remove stray isort diff log`（本次运行）。本文只把本次实际读取的代码/配置与实际执行的命令输出当作实现事实；任务给出的 PRD 摘要仅作为目标契约。路径均相对仓库根目录。
>
> 范围：JWT/RBAC、SSE/Redis、告警生命周期、通知、文件上传、路径与 SSRF 边界，并记录可复用模式、契约风险、测试命令和缺口。除本报告外，本次没有修改源码、配置、迁移或测试。

## 1. 结论摘要

1. **已有可复用的认证骨架，但当前不能直接视为可靠安全边界。** JWT 使用固定 HS256 对称密钥，登录返回 24 小时 token；`require_role(admin/operator)` 已用于模型上传/切换、流控制、告警确认、设备写入和导出（`backend/app/core/security.py:33-71,108-135`；`backend/app/api/routes.py:177-209,315-378,653-659,803-810,864-869,955-1016`）。但 `decode_access_token()` 不检查 `type == "access"`、issuer/audience，`require_auth` 缺 token 时会解引用 `None`（`backend/app/core/security.py:74-115`）；数据库不可用时还有固定明文 `admin/Admin@123456` fallback（`backend/app/api/routes.py:93-126`）。本次探针实际确认：带 `type="not-access"` 的签名 token 可被解码；坏 role 会抛 `ValueError`；`require_auth(None)` 会抛 `AttributeError`。
2. **SSE/Redis 拓扑已存在，但事件契约和可靠性不满足 forecast 告警。** Redis Pub/Sub 把跨进程消息转给进程内 SSE manager；SSE 有客户端队列、15 秒心跳、1000 条内存历史和 `Last-Event-ID` replay（`backend/app/core/redis_client.py:24-41,81-148`；`backend/app/services/sse_manager.py:32-43,52-120,142-189`；`backend/app/main.py:78-94,143-152`）。然而 SSE 完全无认证、无租户/角色过滤；replay 不跨进程/重启；生产者先直接广播又发 Redis，同一进程会收到自己发布的消息而重复广播；当前 producer 没有写 Alert 数据库；REST/SSE 告警 ID 形状也不一致。PRD 要求的 `forecast`、lead time、`simulation_id` 和 30 分钟抑制均不存在。
3. **现有告警仅是旧设备/AI 告警，生命周期不完整。** ORM 有 acknowledged/resolved 布尔值和确认人/时间，确认 API 可做 acknowledge/resolve/ignore（`backend/app/models/db_models.py:180-224`；`backend/app/schemas/schemas.py:149-152`；`backend/app/api/routes.py:864-891`），前端有确认/解决 UI（`frontend/src/pages/AlertCenter.tsx:136-193`）。但没有状态机、版本号、审计事件、幂等键、通知投递状态、dedup/suppression key、forecast/occurred 视觉语义；`ignore` 没有独立持久状态；找不到告警仍返回成功；生产者和 SSE payload 不落库。Dashboard 的告警确认甚至只改本地 Zustand，不调用 API（`frontend/src/pages/Dashboard.tsx:421-445`）。
4. **通知是同步、尽力而为、无去重/重试/投递记录。** critical/warning 会顺序发 SMTP 和 webhook，失败只返回 `False`，调用方忽略结果（`backend/app/services/notification_service.py:27-107`；`backend/app/workers/inference_worker.py:191-228`；`backend/app/services/mock_data_generator.py:178-197`）。SMTP 在 async 函数中调用阻塞式 `smtplib`；HTML 直接插入未转义的 title/description；webhook URL 来自配置、没有出站私网/协议/DNS/重定向防护。若把 webhook URL 暴露成管理员可编辑 API，将直接形成 SSRF。
5. **现有文件上传只有模型上传，且存在高危路径穿越与无限制写盘。** `/api/models/upload` 虽要求 admin 并限制后缀为 `.pt/.onnx/.engine`，但 query `version` 未净化，直接拼入 `MODEL_STORAGE_PATH / f"{version}.{ext}"`；没有 `resolve()` 后根目录包含校验、流式字节上限、MIME/魔数/模型结构验证、原子替换或覆盖策略（`backend/app/api/routes.py:204-246`；`backend/app/core/config.py:64-72,102-105`）。本次路径探针对 `../escape` 和 `..\escape` 均得到 `inside=False`。`MAX_UPLOAD_SIZE_MB=100` 仅是配置项，在 `backend/app` 无使用命中。
6. **存在三处直接出站请求边界，均未做 SSRF 防护。** 公共 `POST /api/inference/detect` 的 `image_url` 被 `httpx.get()` 原样请求；流启动把用户提供的 URL 交给 FFmpeg/OpenCV；通知 webhook 原样 POST（`backend/app/api/routes.py:273-307,315-333`；`backend/app/services/inference_service.py:165-199`；`backend/app/services/stream_service.py:71-123,252-327`；`backend/app/services/notification_service.py:59-70`）。没有 scheme/host allowlist、私网/loopback/link-local/保留 IP 拦截、DNS 解析后校验、每次 redirect 再校验、响应大小上限或协议降级防护。
7. **实时与控制通道有未授权高权限面。** `GET /api/sse/events` 无认证依赖，会向任何连接者广播设备/告警；`WebSocket /api/ws/control` 无 JWT/RBAC，接收 `device_control` 并对 `restart` 返回 `status: executed`（`backend/app/api/routes.py:393-413,421-529`；`frontend/src/hooks/useWebSocket.ts:26-65`）。此外 mock 配置/启停是未认证写接口（`backend/app/api/routes.py:1039-1076`）。
8. **配置与部署默认值包含固定凭据/密钥，并直接暴露数据库、Redis、Influx 端口。** 固定值位于 `backend/app/core/config.py:33-61` 和 `docker-compose.yml:13-18,35-37,53-62,80-95,168-171`；`.env.example` 是占位符模板（`.env.example:5-21,42-52`），但默认配置和 compose 仍可无显式 secret 启动。CORS 仅在配置层限制浏览器读取，不能替代认证。

## 2. JWT / RBAC 现状

### 2.1 已实现且值得复用

- **bcrypt 密码哈希与 FastAPI 依赖注入。** `pwd_context` 使用 bcrypt（`backend/app/core/security.py:19-30`）；角色依赖工厂返回闭包，便于给新写接口声明允许角色（`backend/app/core/security.py:118-135`）。
- **角色层级清楚。** ORM 已有 `admin/operator/viewer`（`backend/app/models/db_models.py:35-38`）。当前后端授权矩阵是：
  - admin：模型切换、模型上传、设备删除（`backend/app/api/routes.py:177-209,1012-1016`）；
  - admin/operator：流启动/停止/推理控制、传感器/告警导出、告警确认、设备创建/更新（`backend/app/api/routes.py:315-378,652-659,803-810,864-869,955-1009`）。
- **JWT 有明确过期时间和算法白名单参数。** token 写入 `exp/iat/sub/type`，decode 只接受配置的算法（`backend/app/core/security.py:33-71,74-92`）。这比完全不验证 token 的旧壳更好。
- **前端 axios 统一携带 bearer token，401 清理本地凭据。** `frontend/src/services/api.ts:17-39`；该模式可复用于 forecast/import/simulation 写请求，但不应把 localStorage 当作安全存储。

### 2.2 高优先级风险

| 优先级 | 风险 | 代码证据 / 本次结果 | 影响 |
|---|---|---|---|
| P0 | **数据库故障时固定管理员 fallback。** 任意异常被折叠为 `user=None`，随后允许 `admin/Admin@123456`。 | `backend/app/api/routes.py:93-126`；Alembic 和 init SQL 也插入同一默认密码（`backend/alembic/versions/0001_initial.py:234-241`；`docker/init-db.sql:168-173`）。 | 数据库不可用时仍可签发 admin token；凭证已进入仓库。 |
| P0 | **SSE、WebSocket、推理 URL、mock 写接口无认证。** | `backend/app/api/routes.py:273-307,393-413,421-529,1039-1076`。 | 未登录用户可读实时设备/告警、触发远程 URL 拉取/推流、改变 mock 状态；WebSocket 控制面还未做角色限制。 |
| P0 | **JWT 未校验 token type/issuer/audience/jti。** `type` 只写不验。 | `backend/app/core/security.py:56-71,74-92`。本次命令输出：`invalid_type_accepted not-access`。 | 未来若增加 refresh/服务 token，同一解码函数会默认接受；无法做 token 类型隔离、audience 隔离或即时撤销。 |
| P1 | **`require_auth` 缺凭证会 500。** `HTTPBearer(auto_error=False)` 可返回 `None`，函数直接访问 `.credentials`。 | `backend/app/core/security.py:20,108-115`。本次命令输出：`require_auth_none AttributeError 'NoneType' object has no attribute 'credentials'`。 | 未认证请求契约不稳定，违反预期 401，可能泄露全局异常类型。 |
| P1 | **RBAC 对未知 role 不返回 403，而抛 ValueError。** | `backend/app/core/security.py:127-132`。本次命令输出：`role_bad_claim ValueError 'not-a-role' is not a valid RoleEnum`。 | 伪造/旧版本/损坏 token 可触发 500；应先校验 claim 集合。 |
| P1 | **角色来自 token 自包含 claim，不在每次请求时检查用户 active/当前角色。** | 登录把 role 写进 token（`backend/app/api/routes.py:128-151`），后续仅验签（`backend/app/core/security.py:74-105`）。 | 账号停用、角色降级或密码重置后，旧 token 最长仍可使用配置的 1440 分钟（`backend/app/core/config.py:56-61`）。 |
| P1 | **固定默认 JWT secret/algorithm 和长有效期。** | `backend/app/core/config.py:56-61`；compose 再次写死（`docker-compose.yml:87-89`）。 | 未覆盖环境变量即可用仓库公开密钥伪造任意 admin token。 |
| P1 | **前端路由只检查 token 存在；菜单才按 localStorage role 隐藏。** | `frontend/src/App.tsx:17-24,39-98`；`frontend/src/store/useAppStore.ts:18-33`；`frontend/src/components/layout/AppLayout.tsx:71-110`。 | 用户可直接输入 `/settings` 等 URL；localStorage role 可篡改。安全必须由后端保证，但 UI 会暴露无效操作和误导用户。 |
| P1 | **配置的速率限制没有实现。** | `RATE_LIMIT_PER_MINUTE` 只定义于 `backend/app/core/config.py:102-105`；`rg -n "RATE_LIMIT_PER_MINUTE" backend/app` 仅命中定义。 | 登录、URL 拉取、WebSocket/stream 启动、导出和上传都没有应用层限流。 |

### 2.3 建议锁定的认证契约（供水力 API 复用）

1. 将 `require_auth` 改成显式 `credentials: HTTPAuthorizationCredentials | None`，缺 token 统一 401；`require_role` 基于它，不重复解析。
2. `jwt.decode` 后显式要求 `type == "access"`、固定 `iss`/`aud`，角色必须属于枚举；增加 `jti` 或 token version，支持登出/停用/改角色的即时失效。
3. 禁止生产 fallback admin；初始凭证只能通过一次性 secret/bootstrap 命令注入，强制首次改密。
4. 密钥/数据库/Redis/Influx/Grafana 全部改为必填 secret 或启动时拒绝弱默认值；基础设施端口默认不映射宿主机。
5. 为 `networks/import/validate/export/seed`、`rainfall-scenarios`、`simulations` 写接口统一使用依赖注入角色：
   - viewer：只读 forecast/risk/results；
   - operator：创建/复制情景、提交/取消仿真、告警确认；
   - admin：删除、seed、模型/配置/通知目标管理。
6. **SSE 认证不能直接套 axios header。** 浏览器原生 `EventSource` 不支持自定义 Authorization header（当前代码 `frontend/src/hooks/useSSE.ts:47-57`）。可选方案是同站点 HttpOnly cookie，或使用 fetch-based SSE/polyfill；不要把长期 JWT 放 query string（会进入日志/历史记录）。若保持原生 EventSource，应定义短时、一次性的 SSE ticket。
7. WebSocket 握手必须校验 JWT/角色，并限制 `device_control.restart` 为 admin/operator；连接建立后不能只依赖前端隐藏按钮。

## 3. SSE / Redis 现状与契约风险

### 3.1 当前数据流

```text
Mock/Inference producer
  ├─ 直接 sse_manager.broadcast_alert(...)
  └─ redis_client.publish_alert(...)
          ↓ Redis Pub/Sub
app lifespan handler -> sse_manager.broadcast("alerts", message) -> EventSourceResponse
```

- 启动时 Redis handler 把 `scn:alerts/sensor_data/model_status` 映射为 SSE `alerts/sensors/system`（`backend/app/main.py:78-94,143-152`）。
- Redis 使用 `redis.asyncio.from_url(..., decode_responses=True, max_connections=10)`，Pub/Sub handler 逐个执行并隔离异常（`backend/app/core/redis_client.py:50-62,106-148`）。
- SSE manager 为每个客户端建立 `asyncio.Queue(maxsize=256)`；广播失败/队列满时丢事件并断开客户端（`backend/app/services/sse_manager.py:64-76,90-120`）。15 秒 comment heartbeat 和 `Last-Event-ID` replay 在 `142-189`。
- Nginx 对 SSE 关闭 buffering/cache，设置 86400 秒 read timeout（`docker/nginx/conf.d/default.conf:42-53`）。这是可直接复用的代理模式。

### 3.2 风险与缺口

1. **重复广播。** mock 和 inference producer 都先直接 `broadcast_alert`，再 `publish_alert`（`backend/app/services/mock_data_generator.py:185-195`；`backend/app/workers/inference_worker.py:214-222`）；同一应用还订阅 Redis 并再次 broadcast（`backend/app/main.py:78-82,143-150`）。因此同一告警会在本进程至少广播两次。前端只按 ID 去重（`frontend/src/store/useAppStore.ts:102-110`），但 ID 契约当前又不一致。
2. **SSE payload 形状不稳定。** `broadcast_alert()` 包装为 `{"alert": alert_data, "timestamp": ...}`（`backend/app/services/sse_manager.py:132-140`），但真实 producer payload 用 `alert_id`、`timestamp`，没有前端要求的 `id`、`created_at`、`is_acknowledged`、`is_resolved`（`backend/app/workers/inference_worker.py:201-212`；`backend/src` 不存在，实际为 `frontend/src/types/index.ts:45-61`）。本次运行 `SSEManager.broadcast_alert({"alert_id":"x"})` 的队列结果为：`{'event':'alerts','data':'{"alert":{"alert_id":"x"},"timestamp":"..."}','id':'1'}`。这会被前端 `payload.alert` 接收成缺字段对象。
3. **REST/SSE ID 漂移。** REST 返回 `id`（`backend/app/api/routes.py:750-769`），SSE producer 用 `alert_id`（`backend/app/workers/inference_worker.py:201-212`；`backend/app/services/mock_data_generator.py:231-248`）。前端却只定义/使用 `id`（`frontend/src/types/index.ts:45-60`；`frontend/src/store/useAppStore.ts:102-123`）。现有页面因此可能把同一告警当成两条，或把未定义 ID 当去重键。
4. **告警 producer 不持久化。** inference/mock 只 broadcast/publish/notify，没有构造并 `add(AlertModel)`（同上 producer 行）。`GET /api/alerts` 主要读取数据库（`backend/app/api/routes.py:725-800`），所以实时告警刷新页面或重连后可能消失；通知与告警中心不是同一事实源。
5. **replay 仅进程内且全局。** `_event_history` 最多 1000 条，只存在单进程 singleton（`backend/app/services/sse_manager.py:35-43,90-99`）。多 worker、重启、负载均衡切换后 `Last-Event-ID` 不保证可重放；没有 retention、权限过滤或 gap 通知。
6. **没有事件 schema/version/source。** `json.dumps(default=str)` 会把未知对象字符串化（`backend/app/core/redis_client.py:83-89`），契约错误不会在发布端暴露。应使用 Pydantic 模型和显式 `schema_version`。
7. **没有 forecast 事件。** 对 `backend/app`、`backend/tests`、`frontend/src` 执行 `flood_forecast|pipe_surcharge|lead_time_minutes|simulation_id|nowcast|risk-ranking|swmm|geojson` 搜索无输出。当前只有 `sensors/alerts/system/connected`（`backend/app/api/routes.py:393-413`；`backend/app/services/sse_manager.py:122-155`）。
8. **SSE 无认证/授权/背压配额。** 所有连接共享全部事件；没有每用户 topic、租户、网络 scope、最大连接数或速率。当前全局内存队列也没有跨实例容量控制。

### 3.3 建议的 forecast 事件契约

建议固定单一 envelope，并先写 OpenAPI/Pydantic/TypeScript 联合 fixture：

```json
{
  "schema_version": 1,
  "event_id": "019...",
  "event_type": "forecast",
  "occurred_at": "2026-09-24T10:00:00Z",
  "network_id": "uuid",
  "alert": {
    "id": "uuid",
    "type": "flood_forecast",
    "status": "predicted",
    "target_type": "node",
    "target_id": "uuid",
    "expected_at": "2026-09-24T10:35:00Z",
    "lead_time_minutes": 35,
    "depth_m": 0.42,
    "confidence": 0.86,
    "simulation_id": "uuid",
    "suppression_key": "network:node:threshold:bucket"
  }
}
```

契约要求：

- `event_id` 稳定且全局单调/可比较；`Last-Event-ID` 对应该 ID，不使用进程内临时计数。
- 同一业务事件只从**一个发布路径**进入 SSE：推荐 producer 写 durable event/outbox，再由单个 relay 发布到 Redis Stream/Pub/Sub；不要“本地 direct + Redis echo”双发。
- 状态至少区分 `predicted/active/acknowledged/resolved/expired`，`forecast` 视觉不能只靠颜色；应有“预计时刻”和“已发生时刻”。
- 30 分钟抑制键应持久化并带唯一约束；通知、SSE 和数据库事件使用同一个 `alert_id/event_id`，实现 exactly-once business effect（至少幂等）。
- 跨 worker replay 用 Redis Streams/数据库事件表；明确 retention、消费者 ACK、gap/resync 语义。
- 所有 SSE 事件在发送前经过 Pydantic 校验；客户端对未知 `schema_version` 明确降级/报错，不能静默把缺字段对象写入 store。

## 4. 告警生命周期与通知

### 4.1 现状

- ORM：`is_acknowledged/acknowledged_by/acknowledged_at/is_resolved/resolved_at`（`backend/app/models/db_models.py:212-224`）。
- API：单一 `/alerts/{alert_id}/acknowledge` 接受 `acknowledge|resolve|ignore`（`backend/app/schemas/schemas.py:149-152`；`backend/app/api/routes.py:864-891`）。resolve 会隐式先 acknowledge；ignore 仍只设置 acknowledged=true。
- 前端：告警中心合并 SSE + REST 并按 `id` 去重（`frontend/src/pages/AlertCenter.tsx:44-71`），有确认/解决按钮（`136-193`）；Zustand 只保存最多 200 条并本地改状态（`frontend/src/store/useAppStore.ts:99-129`）；浏览器 notification 用 `notifiedIds` 抑制当前页面内重复提示（`frontend/src/hooks/useAlertNotifications.tsx:15-73`）。
- 通知：只处理 critical/warning；SMTP recipients 和 webhook URLs 均来自 Settings（`backend/app/services/notification_service.py:72-107`）。生产 worker 在 SSE/Redis 后直接 await notification（`backend/app/workers/inference_worker.py:214-228`）。

### 4.2 生命周期契约风险

1. **不存在显式状态机。** 同一 API 可重复 acknowledge/resolve；代码不检查当前状态、已解决后重开、角色细分、操作版本或幂等键（`backend/app/api/routes.py:864-891`）。
2. **`ignore` 没有独立状态。** schema 接受 ignore，但 ORM 没有 ignored/ignored_by/ignored_at（`backend/app/schemas/schemas.py:149-152`；`backend/app/models/db_models.py:212-224`）。
3. **找不到告警仍返回 200 `status: ok`。** 查询无结果时直接返回原请求的 action/status（`backend/app/api/routes.py:870-891`）。客户端会误认为状态已改变。
4. **DB 写入没有显式 commit 契约测试。** 这里使用 `get_db_session()` 上下文管理器，正常退出时会 commit（`backend/app/core/database.py:119-129`），但测试夹具错误地把它当 FastAPI dependency 覆盖（`backend/tests/conftest.py:33-42`；路由实际直接 `async with`，如 `backend/app/api/routes.py:870-875`），所以事务/不存在 ID/冲突行为未被可靠测试。
5. **Dashboard 确认不调用后端。** `frontend/src/pages/Dashboard.tsx:441-445` 仅执行本地 `acknowledgeAlert`；同一告警在 AlertCenter 刷新后可能恢复未确认。DeviceDetailPanel 也有本地确认路径（`frontend/src/components/device/DeviceDetailPanel.tsx:188-193`）。
6. **乐观更新吞掉 API 错误。** AlertCenter 先改 store，再 `.catch(() => {})`（`frontend/src/pages/AlertCenter.tsx:136-141,170-189`）。401/403/500 时 UI 仍显示成功，破坏告警事实性。
7. **无审计和通知投递状态。** ORM 没有 `updated_at/version/acknowledged_by` 完整历史、事件表、delivery attempt、provider message ID、retry count、last error。无法回答“谁在何时确认、通知是否送达、是否重复”。

### 4.3 通知风险

- `smtplib.SMTP` 是同步网络调用，却直接放在 `async def` 中，且没有 connect/send timeout（`backend/app/services/notification_service.py:27-57`）；慢 SMTP 会阻塞事件循环和 SSE/worker。
- 多个 recipients/webhooks 顺序 await，没有并发上限、总 deadline、背压或队列（`92-106`）。告警发布延迟会线性增长。
- `send_webhook` 允许配置 URL 原样 POST，无 SSRF 防护、签名、mTLS、响应体限制、redirect 策略或幂等 header（`59-70`）。
- HTML 邮件直接插入 `title/description`（`83-90`），未做 HTML escaping；恶意标题可注入邮件 HTML。
- 失败只返回 False/写日志（`55-57,68-70`），producer 不检查返回值（`backend/app/workers/inference_worker.py:222`）。无自动重试、DLQ、去重或指标。
- 每次检测都可发通知；没有 30 分钟事件窗、渠道冷却、升级策略或静默策略。

### 4.4 建议生命周期与通知模式

1. 用显式状态转换服务替代通用 acknowledge endpoint：例如 `POST /alerts/{id}/transitions`，请求包含 `from_version/to_version/action/reason/idempotency_key`；数据库用 `WHERE id=? AND version=?` 原子更新。
2. 保存不可变 `alert_events` 审计表；告警主表保存当前状态和 `version`。建立约束：resolved 不能直接回退，reopen 必须有原因。
3. “预测/已发生”属于业务阶段，单独字段 `phase=predicted|observed`，不要挤进 acknowledged/resolved。
4. 先持久化告警和 outbox，再异步发 SSE/通知；通知 worker 以 `alert_id + channel + transition_version` 唯一键幂等。
5. 邮件 HTML 使用模板自动转义；webhook 使用 HMAC 签名、事件 ID、幂等 header；投递记录 attempt/status/error/retry_at。
6. critical/warning 策略配置化，包含 30 分钟 suppression、最小升级阈值、渠道 cooldown、失败重试/DLQ；通知延迟不能阻塞 nowcast 主链路。

## 5. 文件上传、路径与资源边界

### 5.1 当前唯一上传入口

`POST /api/models/upload`：

- admin 角色依赖（`backend/app/api/routes.py:204-209`）；
- 仅按 `file.filename.endswith` 检查后缀（`215-219`）；
- `version` 作为 query string直接拼路径（`223-225`）；
- 8 KiB 分块写盘并累计 hash/size，但没有 `MAX_UPLOAD_SIZE_MB` 限制（`227-235`）；
- 响应返回服务器绝对路径（`240-246`）；
- 随后 `ModelManager.scan_repository()` 会 hash/加载候选文件（`236-239`；`backend/app/core/model_manager.py:147-184`）。

### 5.2 已确认风险

1. **路径穿越。** `version="../escape"` / `..\escape` 可逃出模型根目录。本次运行输出：
   ```text
   ../escape \models\weights\..\escape.pt inside False
   ..\escape \models\weights\..\escape.pt inside False
   ok \models\weights\ok.pt inside True
   ```
   对应代码是 `settings.model_storage_dir / f"{version}.{ext}"`，没有 `resolve().is_relative_to(root.resolve())`（`backend/app/api/routes.py:223-225`）。虽然 `Query(...)` 没有长度/字符限制，FastAPI 也不会自动阻止路径分隔符。
2. **磁盘耗尽。** `MAX_UPLOAD_SIZE_MB` 仅定义（`backend/app/core/config.py:102-105`）；本次 `rg -n "MAX_UPLOAD_SIZE_MB" backend/app` 只命中配置定义。上传循环会一直写到 EOF。
3. **扩展名/内容混淆。** 仅后缀检查；没有 magic bytes、模型解析沙箱、压缩包/依赖白名单、内容哈希去重或签名。`.pt` 还涉及 pickle/模型加载供应链风险。
4. **覆盖和部分文件。** 同 version/ext 直接 `open("wb")`，可覆盖现有模型；异常时可能留下部分文件，无临时文件 + fsync + atomic replace。
5. **绝对路径泄露。** 响应 `file_path=str(save_path.resolve())` 暴露容器目录（`backend/app/api/routes.py:240-245`）。
6. **Nginx 与应用上限各自为政。** Nginx `client_max_body_size 100M`（`docker/nginx/conf.d/default.conf:12-13`），应用 Settings 也是 100，但实际应用不执行；直连 backend 8000 可绕过 Nginx。

### 5.3 水力导入/仿真应复用的安全模板

建议建立统一 `safe_file_io`/`safe_path` 服务，而不是在每个路由手写：

- 版本/任务 ID 只接受 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`，禁止绝对路径、分隔符、NTFS device/ADS 名称。
- `candidate = (root / user_value).resolve()`；要求 `candidate.is_relative_to(root.resolve())`，并拒绝符号链接/重解析点逃逸（Windows junction/symlink）。
- 上传/导入采用流式计数，读到 `limit+1` 即中止并删除临时文件；PRD 网络导入上限应低于通用 100MB，但仍要有独立配置。
- 扩展名 + magic + parser 限额；GeoJSON 限最大 bytes/节点/坐标字符串，解析时限制 nesting/depth；INP 限 sections/lines/token 数。
- 每任务使用不可预测的临时目录（例如系统 temp 下 UUID），权限最小化；finally/取消/超时/崩溃恢复都清理；不把用户文件名用作最终存储名。
- 仿真临时目录绝不接受用户路径；solver 只读固定 root 下的已验证输入，输出只写任务目录；禁止 shell 字符串拼接。
- 大文件落盘后 fsync + atomic replace；限制并发上传、磁盘剩余空间和总临时空间。
- 测试必须覆盖 POSIX `/`、Windows `\`、盘符、UNC、`.`/`..`、URL 编码、超长名、同名并发、符号链接/junction、截断/超大/zip bomb/恶意 pickle/GeoJSON 深层嵌套。

## 6. SSRF 与协议边界

### 6.1 直接出站点

| 出站点 | 输入 | 当前行为 | 风险 |
|---|---|---|---|
| `POST /api/inference/detect` | `image_url` | `httpx.AsyncClient(timeout=30).get(image_url)`，无认证 | 任意 URL SSRF、redirect、DNS rebinding、超大响应/解压占用。 |
| `POST /api/streams/start` | `stream_url` | 交给 FFmpeg `-i` 与 `cv2.VideoCapture` | 协议走私、内网 RTSP/HTTP/file/device 访问、长时间占用子进程/worker；operator 即可触发。 |
| webhook | `ALERT_WEBHOOK_URLS` | `httpx.post(url, json=payload)` | 配置/未来管理 API 可 SSRF；无私网限制和签名。 |
| Influx Flux 查询 | `start_time/end_time/device_id` | 直接 f-string 拼 Flux（`backend/app/api/routes.py:665-674`） | 认证 operator 仍可进行 Flux 查询注入；不是 SSRF，但属于出站数据边界。 |

### 6.2 统一防护建议

1. **默认拒绝，而非默认允许。** 只允许明确 scheme：图片 `https`（必要时 `http` 仅内网 allowlist）；流默认只允许 `rtsp(s)`；webhook 只允许 `https`。
2. URL 解析后拒绝 username/password、fragment、非标准端口、IP literal；域名解析全部 A/AAAA 后，任何私网、loopback、link-local、multicast、reserved、IPv4-mapped IPv6 都拒绝。
3. 连接到已验证 IP，同时设置 Host/SNI 目标，防止 DNS rebinding；每次 redirect 重新完整验证，限制 2–3 跳。
4. 限制连接/握手/read/write/pool timeout、总 deadline、响应 bytes/content-type；图片解码前做大小和像素上限。
5. FFmpeg 使用固定参数数组（当前已经是 list，无 shell=True，这是可复用点，`backend/app/services/stream_service.py:270-310`），但还需 `-protocol_whitelist`、网络/设备/file 协议限制、资源限额、独立进程组/cgroup/job object、并发和速率限制。
6. WebSocket 控制与流 URL 分离：viewer 不可启动流；operator 也只能访问设备注册表中预批准的主机/协议；不要接受任意 URL。
7. 在网络层再加 egress proxy/firewall，只允许业务白名单；应用层检查不能替代网络隔离。

## 7. 可复用模式清单

1. **集中 Settings。** `pydantic-settings` 单例和 `.env` 模式可复用（`backend/app/core/config.py:16-24,144-145`）。应扩展仿真并发/超时/重试/20MB、nowcast 周期、抑制窗、保留期、sandbox root、允许 URL schemes/hosts；生产敏感项使用 `SecretStr` 且无弱默认。
2. **FastAPI dependency RBAC。** `require_role(*roles)` 的声明式接口可复用（`backend/app/core/security.py:118-135`），但先修复缺 token/坏 claim/type/issuer/audience。
3. **Pydantic 输入/输出 schema。** 现有 Token、Alert、Inference schema 可作为强类型起点（`backend/app/schemas/schemas.py:45-72,129-153,226-264`）；新 API 必须让路由实际使用它们，而非裸 dict。
4. **Redis 异步封装和 handler 隔离。** 连接、JSON publish、逐 handler 异常隔离可复用（`backend/app/core/redis_client.py:50-62,81-148`），但发布路径必须单一化，事件 replay 改 durable store。
5. **SSE queue + heartbeat + Last-Event-ID 原型。** `frontend/src/hooks/useSSE.ts:28-135` 和 `backend/app/services/sse_manager.py:52-189` 可复用连接 UX；不要复用其进程内历史和全局无授权广播。
6. **Nginx SSE/WS 专用代理。** SSE buffering off/cache off/长 timeout 与 WS upgrade 已配置（`docker/nginx/conf.d/default.conf:27-64`），适合 forecast 长连接。
7. **告警前端去重/通知抑制。** Zustand `id` 去重与 200 条上限（`frontend/src/store/useAppStore.ts:99-110`）、页面内 `notifiedIds`（`frontend/src/hooks/useAlertNotifications.tsx:15-73`）是 UX 原型；ID 契约和持久事实源修复后再用于 forecast。
8. **FFmpeg 参数数组。** 使用 `subprocess.Popen(list, shell=False)`，避免直接命令注入（`backend/app/services/stream_service.py:270-310`）；仍需协议/资源/SSRF 边界。
9. **Prometheus instrumentator 与请求 ID。** `/metrics`、X-Request-ID/X-Process-Time 已存在（`backend/app/main.py:214-218,240-258`）；新增 queue depth、task duration/failure/retry、nowcast→alert latency、SSE client/drop、notification delivery 指标。
10. **异步 SQLAlchemy session 模式。** `async_sessionmaker` + context manager（`backend/app/core/database.py:79-129`）可用于 worker/outbox；生产不要依赖路由内重复手工事务。

## 8. 关键文件索引

| 文件 | 关键符号/行 | 作用与判断 |
|---|---|---|
| `backend/app/core/security.py` | `create_access_token` 33-71；`decode_access_token` 74-92；`require_auth` 108-115；`require_role` 118-135 | JWT/RBAC 起点；缺 token、type/issuer/audience、坏 role 处理不安全。 |
| `backend/app/api/routes.py` | login 93-152；upload 204-246；SSRF inference 273-307；stream 315-385；SSE 393-413；WS 421-529；alerts 725-891；mock 1039-1076 | 当前安全边界主入口；多处未认证写/实时面。 |
| `backend/app/core/config.py` | JWT/Redis 50-61；安全 102-120 | 集中配置可复用；固定 secret、上限未执行。 |
| `backend/app/core/redis_client.py` | connect/publish 50-99；listener 106-148 | 跨进程 Pub/Sub；非 durable stream。 |
| `backend/app/services/sse_manager.py` | clients/history 32-43；connect/broadcast 52-120；replay/heartbeat 142-189 | SSE 原型；内存历史、无租户/授权。 |
| `backend/app/services/notification_service.py` | SMTP 27-57；webhook 59-70；notify 72-107 | 同步通知、SSRF/阻塞/无投递状态。 |
| `backend/app/workers/inference_worker.py` | alert creation/broadcast 191-228；snapshot 230-243 | 告警 producer；不落库、无 dedup，文件名含 camera_id。 |
| `backend/app/services/mock_data_generator.py` | loop 147-200；alert 231-248 | 告警/传感器生产；与真实 producer 同样直发+Redis。 |
| `backend/app/services/inference_service.py` | URL fetch 165-199 | 直接 SSRF、响应无大小限制。 |
| `backend/app/services/stream_service.py` | start 71-123；FFmpeg 252-327 | 参数数组可复用；任意流 URL/资源边界缺失。 |
| `backend/app/models/db_models.py` | Role 35-38；Alert 180-224 | 告警持久化基础；无 forecast 字段/状态历史/抑制键。 |
| `backend/app/schemas/schemas.py` | Alert 129-153；Inference 226-264 | 现有 DTO；AlertAcknowledge 不足以表达完整状态机。 |
| `frontend/src/hooks/useSSE.ts` | connect/reconnect 28-135 | 原生 EventSource；无 bearer header 方案。 |
| `frontend/src/hooks/useAlertNotifications.tsx` | 15-73 | 浏览器通知；仅页面内去重。 |
| `frontend/src/store/useAppStore.ts` | role 18-33；alerts 99-129 | 客户端角色/告警状态；不可作为安全事实源。 |
| `frontend/src/pages/AlertCenter.tsx` | SSE/REST merge 44-78；actions 136-193 | 告警中心；乐观更新吞错。 |
| `frontend/src/pages/Dashboard.tsx` | SSE 218-235；ack 421-445 | 实时告警入口；确认只改本地。 |
| `frontend/src/components/layout/AppLayout.tsx` | menu roles 71-110 | 菜单级 RBAC；直接路由仍可访问。 |
| `docker/nginx/conf.d/default.conf` | body limit 12-13；API/SSE/WS 27-64 | Nginx 上限与实时代理可复用；与应用上限未统一。 |
| `docker-compose.yml` | fixed secrets/ports 13-18,35-37,53-62,80-95 | 部署安全风险；基础设施默认暴露。 |
| `.env.example` | secrets placeholders 5-21；notifications 42-52 | 正确使用占位符的模板；实际 Settings/Compose 仍有固定默认。 |
| `.github/workflows/ci.yml` | backend test 32-51；frontend 53-83 | CI 命令来源；没有安全/SSRF/SSE 生命周期测试。 |

## 9. 本次实际运行的检查

### 9.1 后端 pytest（临时依赖环境下通过；非目标 Python 3.11）

当前主机 `python --version` 为 `Python 3.14.4`；项目 CPU 镜像目标是 Python 3.11（`docker/backend/Dockerfile.cpu:0`）。本机缺 `pytest-asyncio/aiosqlite/prometheus_fastapi_instrumentator`，因此本次在仓库外临时目录安装缺失测试依赖，再执行完整测试文件集合：

```text
PROJECT='C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage'; TMPBASE=$(mktemp -d); python -m pip install --disable-pip-version-check --quiet --target "$TMPBASE/deps" pytest-asyncio aiosqlite prometheus-fastapi-instrumentator python-json-logger; ... PYTHONPATH="$DEPS;$PROJECT/backend" python -m pytest "$PROJECT/backend/tests" -v --tb=short ...
# collected 14 items
# 14 passed, 1 warning in 0.25s
```

这只证明当前 14 个测试在主机 Python 3.14 + 临时依赖下通过；**Python 3.11/CI 镜像测试未运行**。这些测试只覆盖 login 成功/失败/缺字段、设备/告警/传感器基本 200、健康状态和 SSE 单例/连接/广播（`backend/tests/test_api/test_auth.py:3-25`；`backend/tests/test_api/test_devices.py:3-27`；`backend/tests/test_api/test_health.py:3-17`；`backend/tests/test_services/test_sse_manager.py:7-31`），不覆盖本文安全风险。

### 9.2 前端 Vitest（失败）

```text
npm --prefix '.../smart-city-drainage/frontend' test
# Test Files 1 failed | 1 passed (2)
# Tests 2 failed | 6 passed (8)
# TypeError: localStorage.clear is not a function
# LoginPage.test.tsx:24
```

当前 Node `v25.9.0`，CI 固定 Node 20（`.github/workflows/ci.yml:57-61`），所以不能断言 Node 20 必现；但仓库的 `frontend/src/test/setup.ts:0-1` 只加载 jest-dom，没有显式 localStorage 隔离，这是实际测试资产缺口。

### 9.3 前端 lint / build（通过）

```text
npm --prefix '.../frontend' run lint
# exit 0

npm --prefix '.../frontend' run build
# 4102 modules transformed
# built in 10.61s
# warning: chunks larger than 1000 kB
```

build 通过只证明 TypeScript/Vite 构建；不证明 SSE 认证、RBAC 页面或水力 UI。

### 9.4 Compose 语法（通过）/ 容器运行（未运行）

```text
docker compose -f '.../docker-compose.yml' config --quiet
# warning: attribute `version` is obsolete
# exit 0
```

这只证明 YAML 可解析。随后：

```text
docker info --format '{{.ServerVersion}}'
# failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine
# exit 1
```

因此本次没有运行 `docker compose up`、真实 Redis、PostgreSQL、Alembic、SSE/WS 或 upload 集成测试。

### 9.5 JWT / 路径只读探针

```text
create_access_token(... extra_claims={'type':'not-access'}) -> decode_access_token()
# invalid_type_accepted not-access

await require_auth(None)
# require_auth_none AttributeError 'NoneType' object has no attribute 'credentials'

await require_role(RoleEnum.admin)({'role':'not-a-role'})
# role_bad_claim ValueError 'not-a-role' is not a valid RoleEnum

Settings(MODEL_STORAGE_PATH='/models/weights') + version probes
# ../escape ... inside False
# ..\escape ... inside False
# ok ... inside True
```

这些是直接调用当前函数的最小探针，不是网络渗透测试；它们确认代码路径，不声称可利用性已经被端到端复现。

## 10. 建议的测试命令与新增门禁

### 10.1 现有项目门禁

```bash
# 后端目标环境（CI 同语义）
cd backend
python -m pytest tests/ -v --tb=short

# 前端目标环境（CI 固定 Node 20）
cd ../frontend
npm ci
npm run lint
npm run format:check
npm test
npm run build

# Compose 与空库（Docker daemon 可用时）
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

CI 实际 backend/frontend 命令见 `.github/workflows/ci.yml:17-51,63-83`。本次 `format:check` 未运行，不能报告通过；本次 frontend test 失败。

### 10.2 安全测试应新增（建议 pytest 文件/层级）

1. `tests/security/test_auth_contract.py`
   - 无/坏 Authorization → 401；错误 scheme → 401；`type != access`、错误 iss/aud、过期、坏 role、停用用户/改角色 → 401/403；admin/operator/viewer 写权限矩阵；数据库故障不得签发 fallback admin。
2. `tests/security/test_sse_auth_and_replay.py`
   - 无 ticket/cookie 的 SSE 401；viewer/operator 事件 scope；Last-Event-ID 只重放授权事件；多 worker/restart replay；queue overflow gap/resync；重复 producer 只产生一个业务事件。
3. `tests/security/test_alert_lifecycle.py`
   - acknowledge/resolve/ignore/reopen 状态转换、并发 version 冲突、不存在 ID 404、幂等 key、审计事件、30 分钟 suppression 唯一约束、forecast→observed 转换。
4. `tests/security/test_notification.py`
   - SMTP/webhook 成功/超时/5xx、重试/DLQ、HMAC、HTML escaping、投递幂等；慢 SMTP 不阻塞 event loop。
5. `tests/security/test_upload_paths.py`
   - `/`、`\`、盘符、UNC、`..`、URL 编码、超长名、symlink/junction、同名并发、limit+1、截断文件、magic mismatch、恶意模型/GeoJSON/INP、临时文件清理。
6. `tests/security/test_ssrf.py`
   - `http/https/file/gopher` scheme、localhost、127/8、10/8、172.16/12、192.168/16、169.254/16、IPv6 ULA/link-local/mapped、IPv4 整数/八进制/hex、DNS rebinding、redirect chain、用户信息、异常端口、大响应；图片/流/webhook 分别验证。
7. `tests/security/test_rate_limits.py`
   - login、upload、inference URL、stream start、WS handshake 的 IP/user 限流和 429 Retry-After。

### 10.3 端到端验收

- 用两个 API worker + Redis 启动，验证 SSE 单次投递、跨 worker replay、worker 重启 gap 行为。
- 用恶意上传样本和 SSRF callback server，在隔离测试环境验证阻断；测试不得访问真实内网或公网 metadata。
- WebSocket 未认证/低角色连接应被 close；viewer 不得 start stream/control。
- 真实 SMTP/webhook 沙箱验证 delivery record、重试和签名；不要用生产收件人。
- 目标 Python 3.11 + Node 20 + Docker Compose 全量运行；`docker compose up`、Alembic、Redis auth、live SSE/WS 必须纳入 CI。

## 11. PRD 差距与实施顺序

| PRD 目标（来自任务摘要） | 当前代码事实 | 必须先锁定 |
|---|---|---|
| 写操作沿用 JWT admin/operator | 旧域部分写接口有 RBAC，但 SSE/WS/inference/mock/上传边界不完整；`require_auth` 有 500 缺陷。 | 修认证契约，定义 viewer/operator/admin 矩阵和 SSE/WS 认证。 |
| 新 forecast 进入告警中心、SSE、通知 | 当前 producer 只临时 broadcast/notify，不落库；无 forecast 事件。 | 单一持久 alert + outbox + SSE event schema。 |
| 30 分钟事件窗抑制 | 无 suppression key/表/唯一约束。 | 明确键组成、边界时间、状态变化后是否重新计时。 |
| 告警含目标、预计时刻、lead time、深度、置信度、simulation_id | 旧 Alert ORM 缺这些业务字段，类型枚举无 `flood_forecast/pipe_surcharge`。 | 新 forecast DTO/表/索引/迁移，不塞进无约束 metadata。 |
| 预计/已发生视觉区分 | 现有 UI 只按 acknowledged/resolved 显示状态（`frontend/src/pages/AlertCenter.tsx:217-233`）。 | 增加 `phase`，定义视觉、排序、通知语义。 |
| 现有 API/SSE/告警零回归 | 现有 ID/envelope/确认路径已漂移，前端测试失败。 | 先写 characterization tests，再扩展；不要直接替换旧 envelope。 |
| 无任意文件读写；导入白名单 | 现有模型上传可路径穿越且不限大小。 | 统一 safe path/file I/O，导入格式/大小/结构白名单与恶意样本测试。 |
| 无 SSRF/外站边界 | 推理 URL、任意 RTSP、webhook 均未限制。 | 统一 URL validator + DNS/IP/redirect/response 限制 + 网络 egress。 |

建议顺序：

1. **P0 安全封口：** 删除 fallback admin；JWT 401/claim 契约；SSE/WS 认证；上传路径穿越/大小限制；推理 URL SSRF；固定 secrets/弱默认值。
2. **P0 事实源：** 告警先持久化，再 outbox 驱动 SSE/通知；冻结 `id/event_id/schema_version/phase/suppression_key`。
3. **P1 生命周期：** 显式状态转换、审计、并发 version、30 分钟抑制、通知投递记录/重试/签名。
4. **P1 水力实现：** 在安全边界和迁移门禁稳定后，再加入 networks/import/validate/export、rainfall、simulation、forecast。
5. **P1 验证：** Python 3.11/Node 20/Compose 双 worker E2E、安全负向测试、故障注入和性能门禁。

## 12. 明确未验证项

- 未运行目标 Python 3.11 容器内 pytest、Node 20 前端测试、真实 `docker compose up`、PostgreSQL/Redis、live SSE/WS、Alembic upgrade、SMTP/webhook 沙箱。
- Docker daemon 在本机不可用（本次 `docker info` 输出见 9.4），所以不能声称 compose 启动或安全配置在运行时生效。
- 未对真实内网、云 metadata 或第三方目标做 SSRF 探测；报告只依据代码边界和本地函数/路径探针。
- 未做渗透测试、依赖 CVE 扫描、模型反序列化恶意样本执行或大文件压力测试。
- 其他并行侦察报告已存在于 `docs/hydraulic-twin/recon/`；本报告没有把其中规划/勾选当作实现事实，也未修改它们。
