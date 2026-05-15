# 智慧城市排水系统 — 未完成功能完善 Spec

## Why
项目存在大量硬编码、模拟数据回退、未集成模块和静默错误处理，导致系统无法在真实环境中运行。核心 AI 推理管线断裂，InfluxDB 未集成，Redis 消息总线形同虚设，前端数据映射层丢弃真实值。

## What Changes
- 实现数据库用户认证，移除硬编码管理员账号
- 集成 InfluxDB 传感器数据读写服务
- 将 InferenceWorker 集成到视频流启动流程
- 注册 Redis 消息处理器，连接 Redis 与 SSE 广播
- 修复预设设备 ID 随机生成问题，使用固定 UUID
- 修复模拟数据生成器 `self.interval` 属性 Bug
- 完善告警/设备 API 错误处理，移除静默回退
- 前端设备数据映射使用后端真实值，移除硬编码覆盖
- 前端图表实时更新对接 SSE 传感器数据流
- 前端设置页面对接后端系统状态 API
- 前端视频监控对接真实流管理 API
- LandingPage 统计数据对接真实 API
- 实现 WebSocket 设备控制逻辑
- 实现 DeviceSearchFilter 设备类型筛选
- 新增后端系统状态检查 API 端点

## Impact
- Affected specs: 认证系统、传感器数据管道、AI 推理管线、实时通信、设备管理
- Affected code:
  - 后端: routes.py, main.py, redis_client.py, mock_data_generator.py, inference_worker.py, stream_service.py
  - 新增: services/influxdb_service.py, services/system_status.py
  - 前端: Dashboard.tsx, MapView.tsx, VideoMonitor.tsx, SettingsPage.tsx, LandingPage.tsx, LoginPage.tsx, WaterLevelChart.tsx, FlowTrendChart.tsx, DeviceSearchFilter.tsx, AlertCenter.tsx

## ADDED Requirements

### Requirement: 数据库用户认证
系统 SHALL 从 PostgreSQL 数据库查询用户进行登录验证，而非使用硬编码管理员账号。登录接口 SHALL 支持多用户，密码 SHALL 使用 bcrypt 安全存储。

#### Scenario: 使用数据库用户登录
- **WHEN** 用户提交用户名和密码
- **THEN** 系统从数据库查询对应用户，验证 bcrypt 哈希密码，返回 JWT Token

#### Scenario: 用户不存在
- **WHEN** 提交的用户名在数据库中不存在
- **THEN** 返回 401 错误，提示"用户名或密码错误"

### Requirement: InfluxDB 传感器数据服务
系统 SHALL 提供 InfluxDB 传感器数据读写服务，替代模拟数据生成器作为传感器数据源。

#### Scenario: 写入传感器数据
- **WHEN** 模拟数据生成器或真实设备产生传感器读数
- **THEN** 数据写入 InfluxDB `sensor_data` bucket

#### Scenario: 查询最新传感器数据
- **WHEN** 前端请求 `/api/sensors/latest`
- **THEN** 从 InfluxDB 查询每个设备最新一条读数并返回

#### Scenario: 查询历史传感器数据
- **WHEN** 前端请求 `/api/sensors/history/{device_id}`
- **THEN** 从 InfluxDB 按时间范围和间隔聚合查询返回

#### Scenario: InfluxDB 不可用
- **WHEN** InfluxDB 连接失败
- **THEN** 降级使用模拟数据，但响应中标注 `"source": "mock"`

### Requirement: AI 推理 Worker 自动集成
系统 SHALL 在视频流转码启动时自动创建关联的 InferenceWorker，实现视频流 → AI 推理 → 告警的完整管道。

#### Scenario: 启动视频流时自动启动推理
- **WHEN** 调用 `/api/streams/start` 启动 RTSP → HLS 转码
- **THEN** 同时创建 InferenceWorker 加入 worker_pool，开始对视频帧执行 YOLO 推理

#### Scenario: 停止视频流时自动停止推理
- **WHEN** 调用 `/api/streams/{camera_id}/stop` 停止转码
- **THEN** 同时从 worker_pool 移除对应的 InferenceWorker

### Requirement: Redis 消息总线集成
系统 SHALL 通过 Redis Pub/Sub 连接模拟数据生成器与 SSE 广播，实现跨服务消息传递。

#### Scenario: 传感器数据通过 Redis 广播
- **WHEN** 模拟数据生成器产生传感器读数
- **THEN** 数据发布到 Redis `scn:sensor_data` 频道，Redis 监听器接收后通过 SSEManager 广播

#### Scenario: 告警通过 Redis 广播
- **WHEN** 推理 Worker 或模拟生成器产生告警
- **THEN** 数据发布到 Redis `scn:alerts` 频道，Redis 监听器接收后通过 SSEManager 广播

### Requirement: 系统状态检查 API
系统 SHALL 提供后端组件连接状态检查端点，供前端设置页面展示真实状态。

#### Scenario: 查询系统状态
- **WHEN** 前端请求 `/api/system/status`
- **THEN** 返回 PostgreSQL、InfluxDB、Redis、AI 模型的连接/就绪状态

### Requirement: 前端设备数据真实映射
前端 SHALL 使用后端返回的真实设备状态、电量、信号强度，不再硬编码覆盖。

#### Scenario: 显示真实设备状态
- **WHEN** 从 API 获取设备列表
- **THEN** 使用后端返回的 `status`、`battery_level`、`signal_strength` 原值展示

### Requirement: 前端图表 SSE 实时更新
WaterLevelChart 和 FlowTrendChart SHALL 通过 SSE 传感器数据流实时更新，不再使用 Math.random()。

#### Scenario: 图表接收 SSE 实时数据
- **WHEN** SSE 推送新的传感器读数
- **THEN** 图表追加新数据点并移除最旧数据点

### Requirement: 前端视频监控对接流管理 API
VideoMonitor 页面 SHALL 从 `/api/streams/status` 获取活跃流列表，支持启动/停止视频流。

#### Scenario: 显示活跃视频流
- **WHEN** 进入视频监控页面
- **THEN** 从 API 获取活跃流列表并展示对应的 HLS 播放器

### Requirement: 前端设置页面展示真实连接状态
SettingsPage SHALL 从 `/api/system/status` 获取后端组件真实连接状态。

#### Scenario: 显示真实连接状态
- **WHEN** 进入设置页面
- **THEN** 从 API 获取并展示 PostgreSQL、InfluxDB、Redis 的真实连接状态

### Requirement: LandingPage 统计数据对接 API
LandingPage 统计数据 SHALL 从设备列表和告警列表 API 计算得出。

#### Scenario: 显示真实统计
- **WHEN** LandingPage 加载
- **THEN** 从设备 API 和告警 API 计算在线设备数、告警数等统计

### Requirement: WebSocket 设备控制
WebSocket 控制通道 SHALL 实现基本的设备控制操作（如设备重启、状态查询）。

#### Scenario: 发送设备控制指令
- **WHEN** 前端通过 WebSocket 发送 `device_control` 类型消息
- **THEN** 后端执行对应操作并返回执行结果

### Requirement: DeviceSearchFilter 设备类型筛选
DeviceSearchFilter 组件 SHALL 支持按设备类型（manhole_cover、camera 等）筛选。

#### Scenario: 按类型筛选设备
- **WHEN** 用户选择设备类型筛选条件
- **THEN** 设备列表仅显示匹配类型的设备

## MODIFIED Requirements

### Requirement: 登录接口
登录接口 SHALL 从数据库查询用户验证，移除硬编码管理员账号。登录页面 SHALL 移除默认密码显示。

### Requirement: 告警 API
告警列表 API 在数据库为空时 SHALL 返回空列表而非模拟数据。告警确认操作失败时 SHALL 返回错误状态而非静默返回 ok。

### Requirement: 设备 API
设备列表 API 在数据库为空时 SHALL 返回空列表而非预设设备数据。

### Requirement: 模拟数据生成器
模拟数据生成器 SHALL 使用固定 UUID 作为预设设备 ID，修复 `self.interval` 属性 Bug，并通过 InfluxDB 服务写入传感器数据。

## REMOVED Requirements

### Requirement: 硬编码管理员认证
**Reason**: 安全隐患，应使用数据库用户认证
**Migration**: 数据库中已通过 init-db.sql 插入默认管理员

### Requirement: 传感器 API 直接调用 MockDataGenerator
**Reason**: 应通过 InfluxDB 服务查询真实/持久化数据
**Migration**: 新增 InfluxDB 服务层，API 调用 InfluxDB 查询
