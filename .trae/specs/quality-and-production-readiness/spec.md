# 项目质量与生产就绪增强 Spec

## Why
项目功能已基本完成，但缺乏测试覆盖、CI/CD 自动化、代码质量工具、监控告警、日志管理、数据备份等生产级基础设施，同时存在 SSE 重连不完善、InfluxDB 数据保留策略缺失、权限管理简单、缺少数据导出和国际化等业务短板。这些问题直接影响代码质量保障、运维可靠性和用户体验。

## What Changes
- 添加后端单元测试与集成测试框架（pytest + httpx）
- 添加前端测试框架（vitest + @testing-library/react）
- 配置 GitHub Actions CI/CD 流水线
- 配置后端代码质量工具（Black + isort + Flake8 + mypy）
- 配置前端代码质量工具（ESLint + Prettier）
- 启用 Prometheus 监控（已有依赖未集成）
- 完善日志管理（结构化日志 + 文件轮转）
- 修复 SSE 断线重连（retry 字段 + Last-Event-ID + 前端自动重连）
- 配置 InfluxDB 数据保留策略
- 添加数据备份脚本
- 实现告警外部通知（邮件 + Webhook）
- 增强 RBAC 权限管理
- 实现数据导出功能（CSV/Excel）
- 添加前端国际化支持（i18n）
- 提供预训练模型下载脚本

## Impact
- Affected specs: 测试、CI/CD、代码质量、监控、日志、SSE、InfluxDB、权限、通知、导出、i18n
- Affected code: 几乎所有后端和前端模块

## ADDED Requirements

### Requirement: 后端测试框架
系统 SHALL 使用 pytest 作为后端测试框架，提供单元测试和 API 集成测试能力。

#### Scenario: 运行后端测试
- **WHEN** 开发者执行 `pytest` 命令
- **THEN** 自动发现并运行 `backend/tests/` 下所有测试用例
- **AND** 测试覆盖核心服务（inference_service、sse_manager、stream_service、mock_data_generator）

### Requirement: 前端测试框架
系统 SHALL 使用 vitest + @testing-library/react 作为前端测试框架。

#### Scenario: 运行前端测试
- **WHEN** 开发者执行 `npm test` 命令
- **THEN** 自动运行所有前端测试用例
- **AND** 测试覆盖关键页面组件和 hooks

### Requirement: CI/CD 流水线
系统 SHALL 配置 GitHub Actions 自动化流水线。

#### Scenario: 代码推送触发 CI
- **WHEN** 代码推送到 GitHub 仓库
- **THEN** 自动运行后端 lint + 测试、前端 lint + 测试、Docker 镜像构建验证
- **AND** 任一步骤失败时阻止合并

### Requirement: 后端代码质量工具
系统 SHALL 配置 Black（格式化）、isort（导入排序）、Flake8（检查）、mypy（类型检查）。

#### Scenario: 运行代码质量检查
- **WHEN** 开发者执行代码质量检查命令
- **THEN** 自动格式化代码、检查代码规范、执行类型检查

### Requirement: 前端代码质量工具
系统 SHALL 配置 ESLint（代码检查）和 Prettier（格式化）。

#### Scenario: 运行前端代码质量检查
- **WHEN** 开发者执行 `npm run lint` 命令
- **THEN** ESLint 检查代码规范，Prettier 格式化代码

### Requirement: Prometheus 监控
系统 SHALL 启用 prometheus-fastapi-instrumentator，暴露 `/metrics` 端点。

#### Scenario: 访问监控指标
- **WHEN** 访问 `GET /metrics` 端点
- **THEN** 返回 Prometheus 格式的请求计数、延迟、错误率等指标

### Requirement: 结构化日志与轮转
系统 SHALL 使用结构化 JSON 日志格式，并配置文件轮转。

#### Scenario: 查看日志
- **WHEN** 后端运行产生日志
- **THEN** 日志以 JSON 格式输出，包含 timestamp、level、logger、message 字段
- **AND** 日志文件按大小轮转，保留最近 10 个文件

### Requirement: SSE 断线重连
系统 SHALL 完善 SSE 服务端重连提示，前端 SHALL 实现自动重连机制。

#### Scenario: 网络断开后重连
- **WHEN** SSE 连接因网络问题断开
- **THEN** 前端自动在 3 秒后重连
- **AND** 重连成功后恢复实时数据推送

### Requirement: InfluxDB 数据保留策略
系统 SHALL 配置 InfluxDB 数据保留策略，默认保留 90 天。

#### Scenario: 历史数据自动清理
- **WHEN** InfluxDB 中数据超过 90 天
- **THEN** 自动删除过期数据

### Requirement: 数据备份脚本
系统 SHALL 提供数据库备份和恢复脚本。

#### Scenario: 执行数据备份
- **WHEN** 运行备份脚本
- **THEN** 自动备份 PostgreSQL、InfluxDB、Redis 数据到指定目录

### Requirement: 告警外部通知
系统 SHALL 支持邮件和 Webhook 两种外部告警通知方式。

#### Scenario: 产生 critical 级别告警
- **WHEN** 系统产生 critical 级别告警
- **THEN** 自动发送邮件通知给配置的收件人
- **AND** 同时触发 Webhook 通知到配置的 URL

### Requirement: RBAC 权限管理
系统 SHALL 实现基于角色的访问控制，支持 admin/operator/viewer 三种角色。

#### Scenario: viewer 角色访问管理接口
- **WHEN** viewer 角色用户尝试创建设备
- **THEN** 返回 403 Forbidden 错误

### Requirement: 数据导出功能
系统 SHALL 支持将传感器历史数据和告警记录导出为 CSV/Excel 格式。

#### Scenario: 导出传感器数据
- **WHEN** 用户点击导出按钮
- **THEN** 下载包含指定时间范围传感器数据的 CSV 文件

### Requirement: 前端国际化
系统 SHALL 支持中文和英文两种语言，使用 i18next 实现。

#### Scenario: 切换语言
- **WHEN** 用户在设置页面切换语言为 English
- **THEN** 所有界面文本切换为英文

### Requirement: 预训练模型下载脚本
系统 SHALL 提供脚本自动下载 YOLOv8 预训练模型权重。

#### Scenario: 首次部署
- **WHEN** 运行模型下载脚本
- **THEN** 自动下载 YOLOv8n 预训练权重到 models/weights/v1/ 目录

## MODIFIED Requirements

### Requirement: SSE 事件流
原实现缺少 `retry:` 字段和 Last-Event-ID 支持。修改后：
- 服务端 SHALL 在 SSE 流中发送 `retry: 3000` 字段
- 服务端 SHALL 支持 Last-Event-ID 请求头，实现断线续传
- 前端 EventSource SHALL 配置自动重连和错误处理

### Requirement: 用户角色
原实现 role 为自由文本 String(32)。修改后：
- role SHALL 使用 Enum 类型，限定为 admin / operator / viewer
- API 端点 SHALL 根据角色进行访问控制
- JWT Token SHALL 包含 role 信息

## REMOVED Requirements

（无移除项）
