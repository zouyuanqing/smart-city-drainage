# Tasks

## Phase 1: 代码质量基础设施

- [x] Task 1: 配置后端代码质量工具
  - [x] 1.1: 创建 `backend/pyproject.toml`，配置 Black、isort、Flake8、mypy
  - [x] 1.2: 添加 `backend/.flake8` 配置文件（忽略 E501 等）
  - [x] 1.3: 在 `backend/requirements.txt` 添加 black、isort、flake8、mypy 开发依赖
  - [x] 1.4: 运行 Black + isort 格式化所有后端代码

- [x] Task 2: 配置前端代码质量工具
  - [x] 2.1: 安装 ESLint + Prettier 及相关插件（eslint-config-prettier、@typescript-eslint 等）
  - [x] 2.2: 创建 `frontend/eslint.config.js` 扁平配置
  - [x] 2.3: 创建 `frontend/.prettierrc` 配置
  - [x] 2.4: 运行 Prettier 格式化所有前端代码
  - [x] 2.5: 修复 ESLint 报告的错误

## Phase 2: 测试框架搭建

- [x] Task 3: 搭建后端测试框架
  - [x] 3.1: 创建 `backend/tests/` 目录结构（conftest.py、test_api/、test_services/）
  - [x] 3.2: 在 conftest.py 中配置 pytest-asyncio、测试数据库、Mock 客户端
  - [x] 3.3: 添加 pytest、pytest-asyncio、httpx 到 requirements.txt
  - [x] 3.4: 编写核心 API 端点测试（health、auth/login、devices、alerts）
  - [x] 3.5: 编写 MockDataGenerator 单元测试
  - [x] 3.6: 编写 SSE Manager 单元测试

- [x] Task 4: 搭建前端测试框架
  - [x] 4.1: 安装 vitest、@testing-library/react、@testing-library/jest-dom、jsdom
  - [x] 4.2: 创建 `frontend/vitest.config.ts` 配置
  - [x] 4.3: 编写 useAppStore hook 测试
  - [x] 4.4: 编写 LoginPage 组件渲染测试
  - [x] 4.5: 编写 api.ts 服务层 Mock 测试

## Phase 3: CI/CD 流水线

- [x] Task 5: 配置 GitHub Actions CI
  - [x] 5.1: 创建 `.github/workflows/ci.yml`，配置后端 lint + 测试 Job
  - [x] 5.2: 配置前端 lint + 测试 Job
  - [x] 5.3: 配置 Docker 镜像构建验证 Job
  - [x] 5.4: 配置 PR 触发和 main 分支推送触发

## Phase 4: 监控与日志

- [x] Task 6: 启用 Prometheus 监控
  - [x] 6.1: 在 main.py 中初始化 PrometheusInstrumentator
  - [x] 6.2: 配置自定义指标（推理延迟、活跃流数、SSE 连接数）
  - [x] 6.3: 在 docker-compose.yml 中添加 Prometheus + Grafana 服务
  - [x] 6.4: 提供基础 Grafana 仪表盘 JSON 配置

- [x] Task 7: 完善日志管理
  - [x] 7.1: 将 logging.basicConfig 替换为 python-json-logger 结构化日志
  - [x] 7.2: 配置 RotatingFileHandler，10MB 轮转，保留 10 个文件
  - [x] 7.3: 添加请求 ID 追踪中间件

## Phase 5: SSE 与数据可靠性

- [x] Task 8: 完善 SSE 断线重连
  - [x] 8.1: 服务端添加 `retry: 3000` 字段到 SSE 流
  - [x] 8.2: 服务端支持 Last-Event-ID 请求头，实现断线续传
  - [x] 8.3: 前端 useSSE hook 实现自动重连（指数退避，最大 30s）
  - [x] 8.4: 前端 SSE 重连时显示连接状态提示

- [x] Task 9: 配置 InfluxDB 数据保留策略
  - [x] 9.1: 在 docker-compose.yml 添加 `DOCKER_INFLUXDB_INIT_RETENTION: 90d`
  - [x] 9.2: 创建 InfluxDB 初始化脚本，配置 downsampling 任务（原始 90d → 1h 聚合 1 年）

- [x] Task 10: 添加数据备份脚本
  - [x] 10.1: 创建 `scripts/backup.sh`，备份 PostgreSQL + InfluxDB + Redis
  - [x] 10.2: 创建 `scripts/restore.sh`，恢复数据
  - [x] 10.3: 在 README 中添加备份/恢复使用说明

## Phase 6: 业务功能增强

- [x] Task 11: 实现告警外部通知
  - [x] 11.1: 创建 `backend/app/services/notification_service.py`
  - [x] 11.2: 实现邮件通知（SMTP），支持 HTML 模板
  - [x] 11.3: 实现 Webhook 通知（HTTP POST JSON）
  - [x] 11.4: 在 config.py 中添加通知相关配置项
  - [x] 11.5: 在告警产生时触发通知（集成到 InferenceWorker 和 MockDataGenerator）
  - [x] 11.6: 前端设置页面添加通知配置 UI

- [x] Task 12: 增强 RBAC 权限管理
  - [x] 12.1: 定义 RoleEnum（admin/operator/viewer）和权限映射
  - [x] 12.2: 创建 `require_role()` 依赖注入装饰器
  - [x] 12.3: 修改 db_models.py User.role 使用 Enum 类型
  - [x] 12.4: JWT Token 中包含 role 信息
  - [x] 12.5: 为所有管理端点添加角色检查（admin only / operator+ / viewer 只读）
  - [x] 12.6: 前端根据角色隐藏/禁用管理操作

- [x] Task 13: 实现数据导出功能
  - [x] 13.1: 添加 `GET /api/sensors/export` 端点，支持 CSV/Excel 格式
  - [x] 13.2: 添加 `GET /api/alerts/export` 端点，支持 CSV/Excel 格式
  - [x] 13.3: 前端告警中心和传感器页面添加导出按钮

- [x] Task 14: 前端国际化（i18n）
  - [x] 14.1: 安装 react-i18next、i18next、i18next-browser-languagedetector
  - [x] 14.2: 创建 `frontend/src/i18n/` 目录，配置 zh.json 和 en.json 语言包
  - [x] 14.3: 提取所有硬编码中文到语言包
  - [x] 14.4: 在 App.tsx 中初始化 i18next
  - [x] 14.5: 设置页面添加语言切换控件

- [x] Task 15: 提供预训练模型下载脚本
  - [x] 15.1: 创建 `scripts/download-model.sh`，下载 YOLOv8n 预训练权重
  - [x] 15.2: 在 README 中添加模型下载说明

# Task Dependencies
- Task 1-2 可并行（后端和前端代码质量工具互不依赖）
- Task 3 依赖 Task 1（测试框架需要代码质量工具先就位）
- Task 4 依赖 Task 2
- Task 5 依赖 Task 3 + Task 4（CI 需要测试框架先搭建好）
- Task 6-7 可并行（监控和日志互不依赖）
- Task 8 独立（SSE 重连不依赖其他任务）
- Task 9-10 可并行
- Task 11 独立
- Task 12 独立
- Task 13 依赖 Task 12（导出端点需要权限控制）
- Task 14 独立
- Task 15 独立
