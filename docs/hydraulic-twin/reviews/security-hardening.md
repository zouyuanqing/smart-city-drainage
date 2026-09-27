# 安全与兼容性加固独立审查

- 模块：`security-hardening`
- 审查日期：2026-09-25
- 结论：**不批准（approved=false）**
- 审查性质：只读代码复核；未修改生产代码或测试。仅写入本报告。
- 阻断原因：发现 2 个高风险问题。其余目标（缺 Bearer 401、固定 fallback 收紧、mock RBAC、模型上传常规穿越/大小限制、Flux 参数绑定、legacy SSE/WS 基本 header 门禁）有实质改进，但不足以抵消下列高风险边界缺口。

## 1. 冻结契约与审查口径

本次按以下材料复核：

1. 任务规格：修复硬编码登录回退、缺失 Bearer 500、上传路径穿越/大小限制、Flux/URL SSRF、mock 写接口无鉴权、SSE/WS 暴露和模型路径问题；在不破坏公开监控读取契约的前提下保护新写接口。
2. 冻结契约：
   - `docs/hydraulic-twin/PRD_SUPPLEMENT.md:286-291`：缺/坏 token 为 401、角色不足为 403；SSE token 只放 Authorization header；生产禁用固定 admin fallback；安全门禁包含停用用户。
   - `docs/hydraulic-twin/PRD_SUPPLEMENT.md:492-502`：后端 RBAC 是最终边界。
   - `docs/hydraulic-twin/PRD_SUPPLEMENT.md:504-539`：401/403/413/422 等稳定错误语义。
   - `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1470-1494`：mutation/RBAC 矩阵及认证要求。
   - `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1743-1754`：JWT、inactive、HS-RS confusion、SSE header、限流安全门禁。
   - `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1818-1823`：canonical forecast SSE 必须 header auth；legacy SSE 行为保持不变。
   - `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2015-2018`：冻结生产 JWT 配置为 `JWT_ALGORITHMS=RS256`。
3. 实现声明：`docs/hydraulic-twin/implementation/security-hardening.md`。
4. 真实实现与新增测试：任务列出的 5 个生产文件及 `backend/tests/security/**`。

## 2. 高风险发现

### HIGH-1：inactive 校验只覆盖部分认证入口，admin 的失效 token 仍可写水力网络并进入 WebSocket

**问题**

共享 `require_auth`/`get_current_user` 路径会调用 `_validate_identity_claims()`，但直接解析 token 的入口没有复用该校验：

- legacy SSE `_claims_from_bearer_header()` 只调用 `decode_access_token()`；
- WebSocket `_require_websocket_role()` 同样直接检查 role；
- 水力 API 的 `_authenticate()` 也直接调用 `decode_access_token()` 后仅检查 role。

因此，签名有效但 `is_active=false` 的 admin token：

- 会被 `/api/networks` 接受；
- 能以 `POST /api/networks` 创建 draft；
- 能通过 `/api/ws/control` 的 admin role 检查；
- 在 staging/production 能通过 legacy SSE 的认证检查。

这与任务目标中的“inactive 用户”和冻结门禁“停用用户”不一致。更重要的是，当前 token 中 `is_active` 来自签发时快照；用户账号后来被停用时，旧 token 仍保持 `true`，而这些入口也没有数据库 active/role 状态复核。实现摘要在 `docs/hydraulic-twin/implementation/security-hardening.md:127` 也明确承认尚未实现签发后的即时撤销。

**代码证据**

- `backend/app/core/security.py:154-162`：只有调用 `_validate_identity_claims()` 才拒绝 `is_active=false`。
- `backend/app/core/security.py:314-338`：共享认证依赖会调用该校验。
- `backend/app/api/routes.py:82-106`：SSE/WS 直接 decode 后只检查 role。
- `backend/app/api/routes.py:109-123`：部署态 legacy SSE 不检查 active。
- `backend/app/api/hydraulic_routes.py:109-122`：水力认证只检查 role，不检查 active。
- `backend/app/api/hydraulic_routes.py:125-138`：viewer/operator/admin 依赖建立在这个不完整认证结果上。
- `backend/tests/security/test_auth_hardening.py:83-91`：只验证 legacy mock 路由，未覆盖水力 API 或 WebSocket。

**本次诊断证据**

执行：

```text
python -c "... create_access_token(extra_claims={'role':'admin','is_active':False}); POST /api/networks ..."
```

输出：

```text
201 INACTIVE-HTTP draft
```

执行 inactive viewer 的水力读：

```text
GET_NETWORKS 200 {'schema_version': None, 'id': None, 'status': None, 'items': [], ...}
```

执行 WebSocket role helper：

```text
viewer accepted False close 1008
admin accepted True close None
```

执行部署态 legacy SSE helper：

```text
viewer ACCEPT
admin ACCEPT
```

对照共享校验函数，同一 inactive admin payload 得到：

```text
REJECTED 401 用户已停用
```

这证明缺口来自入口绕过共享校验，而不是 token 解析结果不一致。

**影响**

停用账号的既有 token 仍可继续创建/修改水力资源、保持高权限控制连接；在未来存在 refresh/service token 或其它直接 decoder 调用时，边界会继续漂移。该问题同时破坏认证一致性和 401 契约。

**修复要求**

1. 提取唯一的“已验证 token → active/role principal”入口，让 HTTP、WebSocket、SSE 和水力认证全部复用；任何 `decode_access_token()` 成功都必须经过 active/role 校验。
2. 停用、角色降级、密码重置需即时生效时，引入 token version/`jti` denylist 或按 `sub` 查询当前用户状态与角色；不能只信任自包含的 `is_active`/`role` 快照。
3. 新增 HTTP 水力读/写、WebSocket、legacy/canonical SSE 的 inactive-token 表驱动测试；至少覆盖 viewer/admin 和已签发后数据库停用。

### HIGH-2：SSRF 预检与实际连接分离，存在 DNS rebinding / TOCTOU；stream 路径还未限制 FFmpeg 协议和资源

**问题**

图片 URL 的 `validate_outbound_url()` 会先调用一次 DNS 并检查结果，但 `_fetch_public_image()` 随后把原始 hostname 交给 `httpx` 再解析、连接。预检地址与实际连接地址没有绑定；攻击者控制的 DNS 可在两次解析间从公网地址切换到 loopback/private/link-local。

stream 路径同样只预检 URL，然后交给 FFmpeg 和 OpenCV；媒体进程会再次解析/处理协议，且没有 `protocol_whitelist`、并发/进程资源预算。实现摘要 `docs/hydraulic-twin/implementation/security-hardening.md:47` 将 DNS pinning 留给部署层，但本任务目标明确包含 SSRF 修复，且侦察契约建议“连接到已验证 IP，同时设置 Host/SNI”。

**代码证据**

- `backend/app/core/security.py:129-139`：预检阶段单独执行 `socket.getaddrinfo()`，随后只返回原始 URL。
- `backend/app/api/routes.py:130-149`：HTTP client 使用原始 hostname；redirect 只重复预检，不固定已验证 IP。
- `backend/app/core/security.py:76-80`：注释也明确要求 caller 额外处理 redirect/size，但没有要求 caller 固定解析结果。
- `backend/app/api/routes.py:486-493`：stream 预检后把原始 URL传入服务。
- `backend/app/services/stream_service.py:98-119`：URL继续进入 FFmpeg 和 inference worker。
- `backend/app/services/stream_service.py:253-296`：FFmpeg 参数中没有 protocol allow/deny list、进程资源限制或单流总 deadline。
- `backend/tests/security/test_ssrf_boundaries.py:14-64`：仅覆盖一次 DNS 解析结果，不覆盖解析后重绑定、redirect 后的实际连接或 media protocol matrix。

**影响**

已认证 operator 可能借图片 URL 绕过 loopback/private/metadata 边界；RTSP/RTMP(S) 还可能被媒体协议链或解析差异转化为内网访问、长时间子进程/worker 占用。单纯 egress ACL 可以缓解，但不能替代应用要求中的连接固定与媒体协议边界。

**修复要求**

1. HTTP 出站使用受控 resolver/transport：解析一次、验证全部地址，连接固定到已验证 IP，同时保留正确 Host/SNI；每跳 redirect 重复完整流程。部署 egress ACL 作为第二层。
2. stream 默认只允许明确协议，并给 FFmpeg 设置严格 protocol allowlist；限制并发、启动/运行期限、输出/磁盘/CPU/内存，失败时终止进程组/worker。
3. 补 DNS 公网→私网重绑定、跨 host redirect、IPv4 替代文本/映射 IPv6、无 Content-Length 大响应、FFmpeg/OpenCV 协议跳转测试。若保留单元 fake，应明确 live SSRF callback 仍未验证。

## 3. 中风险发现

### MEDIUM-1：生产 JWT 接受混合算法族，偏离冻结的 RS256-only 配置，且缺 HS-RS confusion 负测

**问题**

`decode_access_token()` 根据未验证 header 的算法族选择 HMAC secret 或 RSA public key，并允许 `settings.JWT_ALGORITHMS` 中所有算法。默认列表是 `["HS256", "RS256"]`；production 校验只要求“包含 RS256”，允许 `["HS256", "RS256"]`。冻结配置却是 `JWT_ALGORITHMS=RS256`，安全门禁明确要求 HS-RS confusion 负测。

这不是简单的“未知 alg”拒绝问题：系统有意接受两个密钥域签发的 token，扩大了生产信任面，也使轮换/误配更难审计。新增安全测试没有覆盖混合族攻击面。

**代码证据**

- `backend/app/core/security.py:24-26`：安全算法集合同时包含 HMAC、RSA、ECDSA。
- `backend/app/core/security.py:190-199`：按 token header 选择 HMAC secret 或 asymmetric public key。
- `backend/app/core/security.py:288-305`：验证时接受配置 allowlist 中所有算法。
- `backend/app/core/config.py:375`：默认 `JWT_ALGORITHMS=["HS256", "RS256"]`。
- `backend/app/core/config.py:461-469`：production 只要求列表包含 RS256。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1747-1752`：要求锁定算法和 HS-RS confusion 负测。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:2015-2017`：冻结为 RS256、issuer、audience。
- `backend/tests/test_security_tokens.py:80-94`：只测 allowlist 外算法和同一 HMAC 族内算法，不测混合族。

**本次诊断证据**

使用有效 RSA key material、把配置设为 `["HS256","RS256"]` 后分别签发 HS256/RS256 token，输出：

```text
HS256 True
RS256 True
```

production Settings 组合探针中 `['HS256','RS256']` 为 `OK`。

**修复要求**

production 强制与冻结契约一致的单一算法族/算法（至少不得接受 HMAC 与 RSA 混合）；补 alg-family confusion、错误 key、配置误组合的启动失败和 token 拒绝测试。

### MEDIUM-2：legacy SSE 的部署态收紧违反“行为保持不变”的冻结兼容契约，且现有浏览器客户端不能发送 header

**问题**

实现把 `/api/sse/events` 在 staging/production 改为 Bearer 门禁，但冻结契约明确说 legacy endpoint 行为保持不变。任务又要求不破坏公开监控读取契约。实现摘要将其描述为需 owner 确认，但审查不能把未确认的契约变更视为通过。

同时 canonical forecast SSE 要求 header Bearer 是合理的，但 legacy SSE 是现有公开监控流；不能直接套用 canonical 认证而造成部署态零回归失败。

**代码证据**

- `backend/app/api/routes.py:109-123`：legacy SSE 在 staging/production 要求 token。
- `backend/app/api/routes.py:563-584`：legacy route 调用该门禁。
- `backend/tests/security/test_auth_hardening.py:133-136`：测试反而固化了 production legacy SSE 401。
- `docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1818-1823`：canonical `/api/forecast/events` 要认证；legacy `/api/sse/events` 行为保持不变。
- 任务规格：要求 SSE/WS 暴露加固，但不得破坏公开监控读取契约。
- 实现摘要 `docs/hydraulic-twin/implementation/security-hardening.md:125-126` 承认该环境差异和浏览器 header 限制。

**影响**

依赖现有公开 SSE 的监控客户端在 staging/production 失去连接；新增测试只证明实现选择了更严格行为，不能证明兼容契约成立。

**修复要求**

保持 legacy SSE 公开行为，或先由契约 owner 正式修订冻结条款并提供可部署的 header-capable client；canonical forecast SSE 继续强制 Authorization header。不得用“需要确认”把已知契约冲突留到发布后。

### MEDIUM-3：WebSocket header-only 认证与当前浏览器实现不兼容

**问题**

后端只接受 WebSocket Authorization header；浏览器原生 `WebSocket` 构造函数不能设置该 header。当前前端 hook 直接 `new WebSocket(wsUrl)`，因此所有浏览器控制连接都会在 accept 前被 1008 关闭。

这可能是安全上可接受的临时断开，但属于明确的功能回归，且尚无集成 owner 变更。

**代码证据**

- `backend/app/api/routes.py:89-106`：明确拒绝 query token，只读 header。
- `backend/app/api/routes.py:601-605`：认证失败时不 accept。
- `frontend/src/hooks/useWebSocket.ts:26-33`：浏览器原生 `new WebSocket(wsUrl)`，没有 header/subprotocol/cookie 方案。
- 实现摘要 `docs/hydraulic-twin/implementation/security-hardening.md:126` 已承认此限制。

**修复要求**

选择并实现浏览器可用的安全握手方案（同源 HttpOnly cookie、受控 WebSocket subprotocol/ticket 等），并补真实 ASGI WebSocket 握手测试；不要把长期 JWT 放 query string。

### MEDIUM-4：模型版本白名单仍接受 Windows 设备保留名，上传在 Windows 可成功创建 `CON.pt`

**问题**

版本正则允许 `CON`、`NUL`、`PRN`、尾随点等 Windows 特殊/非便携名称。虽然 `resolve().is_relative_to(root)` 能阻止普通路径穿越，但这不能替代平台文件名语义；在当前 Windows 主机，模型上传诊断实际返回 200 并创建 `CON.pt`。

**代码证据**

- `backend/app/core/model_manager.py:36-52`：只限制字符、长度和首字符，未拒绝 Windows reserved device stems、尾随点或相关模式。
- `backend/app/api/routes.py:353-385`：验证通过后用 `os.replace()` 发布。
- `backend/tests/security/test_upload_paths.py:27-129`：未覆盖 reserved device names、尾随点、ADS/UNC 的完整平台矩阵。

**本次诊断证据**

对 `MODEL_VERSION_RE` 探针：

```text
'CON' True
'NUL' True
'v1.' True
'v1..' True
```

对真实 `/api/models/upload` 探针：

```text
200 'CON.pt' True
```

**修复要求**

按目标部署 OS 拒绝 Windows reserved device names（大小写不敏感、含扩展名 stem）、尾随点/空格和冲突的 8.3/短名；增加 Windows CI fixture。

## 4. 已确认的改进（不抵消高风险发现）

1. `require_auth` 缺凭证稳定返回 401，并带 `WWW-Authenticate: Bearer`：`backend/app/core/security.py:328-338`。
2. 未知 role 在共享依赖中返回 403，而不是 `ValueError`：`backend/app/core/security.py:154-162,341-365`。
3. 固定 demo admin 已改为显式非生产开关，数据库故障默认不再签发 token：`backend/app/api/routes.py:206-251`；开关属性在 `backend/app/core/config.py:499-510`。
4. mock 写接口已加 operator/admin 依赖：`backend/app/api/routes.py:1219-1251`。
5. 模型上传采用扩展白名单、8 KiB 流式读取、limit+拒绝、同目录临时文件、fsync 和原子替换，响应只返回 basename：`backend/app/api/routes.py:331-404`。
6. legacy latest/history/export 的用户值已通过 Influx Query API `params` 绑定，数值历史范围有边界：`backend/app/services/influxdb_service.py:126-292`。
7. 图片 URL 已限制 scheme/port/userinfo/fragment、逐跳检查 redirect、关闭自动 redirect、限制响应 10 MiB：`backend/app/api/routes.py:126-174`。
8. legacy SSE 保持 development 公开；canonical forecast SSE 继续使用 `Depends(_viewer)`：`backend/app/api/routes.py:109-123`、`backend/app/api/hydraulic_routes.py:3859-3889`。
9. rate-limit key 不再保存原始 bearer 字符串：`backend/app/main.py:383-389`。

## 5. 本次实际运行的检查

### 5.1 任务指定安全回归

命令：

```text
cd "C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend" && PYTHONPATH='C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' python -m pytest 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/security' -q
```

结果：

```text
36 passed, 1148 warnings in 0.56s
```

该套件在当前主机 Python 3.14 环境通过；未在冻结目标 Python 3.11 环境运行。警告均来自 `pytest-asyncio`/Python 3.14 兼容性。

### 5.2 聚焦认证/水力/安全回归

命令：

```text
cd .../backend && PYTHONPATH=... python -m pytest tests/test_api/test_auth.py tests/api/test_hydraulic_review_round1.py tests/security -q
```

结果：

```text
50 passed, 1950 warnings in 1.37s
```

该检查证明现有测试全绿，但不能覆盖本报告复现的 inactive 入口分叉。

### 5.3 JWT token 测试

命令：

```text
cd .../backend && PYTHONPATH=... python -m pytest tests/test_security_tokens.py -q
```

结果：

```text
14 passed, 403 warnings in 0.04s
```

未包含 mixed HS/RS family 负测。

### 5.4 语法与 diff

命令：

```text
python -m compileall -q app/core/security.py app/api/routes.py app/main.py app/services/influxdb_service.py app/core/model_manager.py tests/security
```

结果：exit 0，无输出。

命令：

```text
git diff --check -- backend/app/core/security.py backend/app/api/routes.py backend/app/main.py backend/app/services/influxdb_service.py backend/app/core/model_manager.py backend/tests/security
```

结果：exit 0，无输出。

## 6. 未运行 / 明确不声称通过

- 按任务要求未重复最终全量门禁；实现摘要中的 `448 passed, 4 skipped, 5 failed` 不是本轮执行证据。
- 未运行 Python 3.11/CI 容器。
- 未运行真实 PostgreSQL/PostGIS、InfluxDB、Redis、Docker/Compose、FFmpeg、真实 WebSocket/SSE 网络端到端。
- 未对真实公网、内网或云 metadata 发起请求；SSRF 结论来自代码路径和受控函数诊断。
- 未做真实 DNS rebinding callback；本报告指出的是预检与客户端二次解析的可利用设计缺口，不声称已完成网络利用复现。
- 未验证模型内容魔数/恶意 pickle；本模块当前只做后缀、路径、大小与原子替换。
- CodeGraph 不可用：项目和上级目录无 `.codegraph/`，工具返回“isn't indexed”；本报告改用真实文件读取与精确文本搜索。

## 7. 最终判定

**approved = false。**

必须先修复 HIGH-1（统一 active principal/即时失效）和 HIGH-2（DNS pinning + stream 协议/资源边界），并补相应负向与集成测试。MEDIUM-1 至 MEDIUM-4 应在同一安全收口轮处理，尤其是 legacy SSE 契约冲突和浏览器 WebSocket 握手兼容性。
