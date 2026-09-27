# 安全与兼容性加固实施摘要

- 日期：2026-09-25
- 仓库：`smart-city-drainage`
- 范围：仅修改任务指定的 5 个生产文件和 `backend/tests/security/**`；未提交、未推送。
- 说明：工作树在本轮开始时已经包含其他工作流的未提交变更；本摘要只记录本轮安全改动，不覆盖或宣称那些并行改动。

## 需求与契约依据

- `docs/hydraulic-twin/SOURCE_PRD.md:25` 要求写操作沿用 JWT admin/operator，`:31` 要求无任意文件读写；本轮保持公开监控读取路径并保护写/控制边界。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:286-291` 要求缺/坏 token 为 401、角色不足为 403，SSE token 只放 Authorization header，固定 admin fallback 禁用。
- `docs/hydraulic-twin/PRD_SUPPLEMENT.md:501`、`:529-536` 固定 401/403/413/422 语义；`docs/hydraulic-twin/IMPLEMENTATION_CONTRACT.md:1470-1494` 固定 mutation/RBAC 矩阵，`:1743-1754` 固定 forecast 安全门禁，`:1818-1823` 说明 legacy SSE 兼容边界。

## 实际改动

### 1. 认证、RBAC 与实时控制

- `backend/app/core/security.py:203-342`
  - JWT 仅接受安全算法集合并继续校验签名、`exp/iat/nbf/sub`、配置的 issuer/audience 和 `type=access`。
  - `create_access_token()` 对 subject、生命周期和 token type 使用服务端值覆盖调用方 extra claims，避免调用方伪造身份或延长过期时间（`:225-247`）。
  - 缺少 Bearer 凭证稳定返回 401，不再对 `None.credentials` 解引用；未知 role 返回 403 而不是抛 `ValueError`（`:315-365`）。
  - 增加 `is_active=False` claim 的拒绝逻辑（`:155-166`）；登录路由对数据库中 inactive 用户直接 401，且仅在非生产显式开关允许时使用 demo fallback（`backend/app/api/routes.py:208-289`）。
  - 增加只接受 Authorization header 的 Bearer 提取器和出站 URL 公共地址校验器（`backend/app/core/security.py:30-139`）。

- `backend/app/api/routes.py:433-479`
  - `/api/inference/detect` 增加 operator/admin RBAC；不再把用户 URL 直接交给原有无限制 URL 拉取方法。
  - `/api/models/switch` 在启动异步线程前校验模型版本。
- `backend/app/api/routes.py:565-700`
  - legacy `/api/sse/events` 在 development 保持原公开行为；staging/production 连接前要求 Bearer（`:110-125`）。这样保留公开监控读取兼容性，同时关闭部署态实时流暴露。
  - `/api/ws/control` 在 accept 前只接受 header Bearer，且仅 operator/admin（`:90-108`、`:594-610`）；不接受 query token。
- `backend/app/api/routes.py:1221-1255`
  - `/api/mock/config`、`/api/mock/start`、`/api/mock/stop` 全部增加 operator/admin RBAC；mock 状态/配置的 GET 读取保持公开。
- `backend/app/main.py:348-399`
  - forecast 既有独立限流逻辑保留；rate-limit key 改为 Authorization 的 SHA-256 摘要，避免在内存 key 中保留原始 bearer token（`:383-389`）。

### 2. 上传、模型路径与出站请求

- `backend/app/core/model_manager.py:37-105,212-245,570-590`
  - 模型版本仅允许 `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`；所有版本到文件名的解析都做 root containment 检查。
  - 拒绝 POSIX symlink 和 Windows reparse-point/junction；扫描仓库时跳过链接/非普通文件，加载时不再信任 registry 中的任意路径。
  - 错误信息不再返回模型根目录绝对路径。
- `backend/app/api/routes.py:333-421`
  - `/api/models/upload` 保留 admin API，但改为扩展名白名单、版本/路径校验、同目录临时文件、流式 `MAX_UPLOAD_SIZE_MB` 限制、`fsync` + 原子替换；超限返回 413、路径错误返回 422，失败清理 `.part` 文件。
  - 响应只返回 basename，不再泄露服务器绝对路径。
- `backend/app/api/routes.py:80-178,481-511`
  - 图片 URL 只允许 HTTP(S) 公共地址；拒绝 userinfo/fragment、非允许端口、loopback/private/link-local/reserved/mapped IPv6 和私网 DNS 答案。
  - 图片下载关闭自动 redirect，每一跳重新校验，最多 3 跳，响应体 10 MiB 上限；RTSP/RTMP(S) 流地址使用同一公共地址校验。
  - 这些是应用层预检/响应限制；网络层 egress/DNS  pinning 仍需部署加固。

### 3. Flux 注入防护

- `backend/app/services/influxdb_service.py:127-292`
  - 最新/历史/导出查询的 bucket、device、时间和过滤值改用 Influx Query API `params` 绑定；用户输入不再拼接进 Flux 源码。
  - 历史查询对 hours/interval/device ID 做边界检查；导出路由改用 `query_sensor_export()`（`backend/app/api/routes.py:829-883`）。

### 4. 回归测试

新增：

- `backend/tests/security/test_auth_hardening.py`：server-owned JWT claims、缺 Bearer 401、inactive 登录/claim、mock 401/403/operator、坏 role 403、部署态 legacy SSE、WebSocket header/RBAC。
- `backend/tests/security/test_ssrf_boundaries.py`：私网/loopback/link-local/IPv6/凭据/端口/协议、私网 DNS、公共 DNS、推理认证与 SSRF 阻断。
- `backend/tests/security/test_upload_paths.py`：POSIX/Windows 路径穿越、超大文件、原子上传/不泄露绝对路径、symlink 模型路径。
- `backend/tests/security/test_flux_injection.py`：latest/history/export 参数化查询和数值边界。

## 实际执行的验证

1. 安全回归（最终代码）：

   ```text
   PYTHONPATH='C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' python -m pytest 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests/security' -q
   36 passed, 1148 warnings in 0.55s
   ```

2. 既有认证测试 + 安全回归：

   ```text
   PYTHONPATH='C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' python -m pytest '.../backend/tests/test_api/test_auth.py' '.../backend/tests/test_security_tokens.py' '.../backend/tests/security' -q
   59 passed, 2153 warnings in 1.34s
   ```

3. 公开监控读取兼容性：

   ```text
   PYTHONPATH='C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' python -m pytest '.../backend/tests/test_api/test_health.py' '.../backend/tests/test_api/test_devices.py' '.../backend/tests/test_services/test_sse_manager.py' -q
   8 passed, 361 warnings in 12.51s
   ```

4. OpenAPI/导入检查：

   ```text
   PYTHONPATH='C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' python -c "from app.main import app; schema=app.openapi(); print('OPENAPI_PATHS', len(schema['paths'])); print('HAS_FORECAST_EVENTS', '/api/forecast/events' in schema['paths']); print('HAS_LEGACY_SSE', '/api/sse/events' in schema['paths'])"
   OPENAPI_PATHS 70
   HAS_FORECAST_EVENTS True
   HAS_LEGACY_SSE True
   ```

5. 语法与 diff 检查：

   ```text
   python -m compileall -q '.../backend/app' '.../backend/tests/security'
   (exit 0)

   git diff --check -- backend/app/core/security.py backend/app/core/model_manager.py backend/app/services/influxdb_service.py backend/app/api/routes.py backend/app/main.py backend/tests/security
   (exit 0)
   ```

6. 完整后端套件（最终代码）：

   ```text
   PYTHONPATH='C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend' python -m pytest 'C:/Users/zyq/Documents/opencode-pn/goal测试/smart-city-drainage/backend/tests' -q
   448 passed, 4 skipped, 5 failed in 20.78s
   ```

   5 个失败均来自本轮开始前已存在的并行工作树状态，而非本轮安全文件断言：

   - migration gate 看到未在 base commit 的 `backend/alembic/versions/0002_hydraulic_schema.py`；
   - dependency handoff 的 `.env.example` SHA-256 不匹配；
   - 前端 `frontend/performance/run-twin-map.mjs` 已存在而旧 gate 仍要求 deferred。

   这些失败与本轮修改的安全测试无关；未越界修复其它 owner 的 migration/dependency/frontend 文件。

## 未运行与风险

- Black/isort/Flake8/mypy 命令已尝试，但当前 Python 环境没有这些模块（分别返回 `No module named ...`）；未宣称 lint/mypy 通过。
- 未运行真实 PostgreSQL/PostGIS、InfluxDB、Redis、Docker/Compose、FFmpeg、WebSocket 网络端到端、真实公网/私网 SSRF callback 或 DNS rebinding 测试；回归中的 DNS/Influx/WebSocket 依赖为本地 fake/unit boundary。
- legacy SSE 在 development 仍按兼容契约公开；staging/production 的 Bearer 要求是本轮安全收紧，需要集成 owner 确认是否接受该环境差异（契约 `IMPLEMENTATION_CONTRACT.md:1823` 仍记录 legacy 行为）。
- WebSocket 现在要求 header Bearer；现有 native browser hook（前端不在本任务 ownedPaths）不会自动发送该 header，需要后续前端/网关集成改造。
- inactive 登录和显式 `is_active=false` token 已 fail closed；尚未在本模块引入数据库 token-revocation/version 检查，因此“已签发后用户被停用”的即时撤销仍需认证/session owner 补齐。
- 模型上传使用现有 `MAX_UPLOAD_SIZE_MB` 配置；水力网络导入的冻结 20 MiB 预算由网络域 owner 负责，本轮未修改其 out-of-scope 实现。
