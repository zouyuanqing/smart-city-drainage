# Tasks

## Phase 1: 后端核心 Bug 修复与基础服务

- [x] Task 1: 修复 MockDataGenerator 的 Bug 和预设设备 ID 问题
  - [x] 1.1: 修复 `self.interval` → `self.sensor_interval` 属性名错误 (mock_data_generator.py L82)
  - [x] 1.2: 将 PRESET_DEVICES 中的 `str(uuid.uuid4())` 替换为固定 UUID 字符串，确保跨重启一致
  - [x] 1.3: 在 PRESET_DEVICES 的固定 UUID 与 init-db.sql 中的设备 ID 保持一致

- [x] Task 2: 实现数据库用户认证，替换硬编码管理员
  - [x] 2.1: 修改 routes.py `/api/auth/login` 端点，从数据库查询用户并验证密码
  - [x] 2.2: 移除硬编码的 ADMIN_USER 字典
  - [x] 2.3: 确保登录失败时返回正确的 401 错误

- [x] Task 3: 完善 API 错误处理，移除静默回退
  - [x] 3.1: 修改 `/api/alerts` 端点，数据库查询失败时返回空列表而非模拟数据
  - [x] 3.2: 修改 `/api/alerts/{alert_id}/acknowledge` 端点，数据库操作失败时返回 500 错误
  - [x] 3.3: 修改 `_get_db_devices()` 函数，数据库查询失败时返回空列表而非预设数据

## Phase 2: InfluxDB 集成

- [x] Task 4: 创建 InfluxDB 传感器数据服务
  - [x] 4.1: 创建 `services/influxdb_service.py`，实现 InfluxDB 客户端初始化与连接管理
  - [x] 4.2: 实现 `write_sensor_reading()` 方法，写入单条传感器数据
  - [x] 4.3: 实现 `write_sensor_batch()` 方法，批量写入传感器数据
  - [x] 4.4: 实现 `get_latest_readings()` 方法，查询每个设备最新一条读数
  - [x] 4.5: 实现 `get_historical_readings()` 方法，按时间范围和间隔聚合查询
  - [x] 4.6: 在 `core/config.py` 中添加 InfluxDB 连接健康检查方法

- [x] Task 5: 集成 InfluxDB 到传感器 API 和模拟数据生成器
  - [x] 5.1: 修改 `/api/sensors/latest` 端点，优先从 InfluxDB 查询，降级时标注 `"source": "mock"`
  - [x] 5.2: 修改 `/api/sensors/history/{device_id}` 端点，优先从 InfluxDB 查询
  - [x] 5.3: 修改 MockDataGenerator，生成数据时同步写入 InfluxDB
  - [x] 5.4: 在 main.py lifespan 中初始化 InfluxDB 客户端

## Phase 3: AI 推理管线集成

- [x] Task 6: 将 InferenceWorker 集成到视频流启动流程
  - [x] 6.1: 修改 stream_service.start_stream()，在转码启动后自动创建 InferenceWorker
  - [x] 6.2: 修改 stream_service.stop_stream()，停止时同时移除 InferenceWorker
  - [x] 6.3: 修改 stream_service.stop_all()，停止所有 Worker
  - [x] 6.4: 在 `/api/streams/status` 响应中增加推理 Worker 状态信息
  - [x] 6.5: 新增 `/api/streams/{camera_id}/inference/start` 和 `/api/streams/{camera_id}/inference/stop` 端点

## Phase 4: Redis 消息总线集成

- [x] Task 7: 注册 Redis 消息处理器，连接 Redis 与 SSE
  - [x] 7.1: 在 main.py lifespan 中注册 Redis 频道处理器（scn:alerts, scn:sensor_data, scn:model_status）
  - [x] 7.2: 处理器逻辑：收到 Redis 消息后通过 SSEManager 广播
  - [x] 7.3: 修改 MockDataGenerator，传感器数据和告警通过 redis_client.publish 发布
  - [x] 7.4: 修改 InferenceWorker，告警通过 redis_client.publish_alert 发布

## Phase 5: 系统状态 API

- [x] Task 8: 创建系统状态检查 API
  - [x] 8.1: 创建 `services/system_status.py`，实现各组件连接状态检查
  - [x] 8.2: 新增 `GET /api/system/status` 端点，返回 PostgreSQL/InfluxDB/Redis/Model 状态
  - [x] 8.3: 在前端 api.ts 中添加 systemAPI.getStatus() 方法

## Phase 6: 前端修复与完善

- [x] Task 9: 修复前端设备数据硬编码覆盖
  - [x] 9.1: 修改 Dashboard.tsx，使用后端返回的真实 status/battery_level/signal_strength
  - [x] 9.2: 修改 MapView.tsx，同上
  - [x] 9.3: 修改 LandingPage.tsx，统计数据从 API 计算得出

- [x] Task 10: 前端图表对接 SSE 实时数据
  - [x] 10.1: 修改 WaterLevelChart.tsx，接收 SSE 传感器数据追加到图表
  - [x] 10.2: 修改 FlowTrendChart.tsx，接收 SSE 传感器数据追加到图表
  - [x] 10.3: 移除图表中的 Math.random() 实时更新逻辑

- [x] Task 11: 前端视频监控对接流管理 API
  - [x] 11.1: 修改 VideoMonitor.tsx，从 `/api/streams/status` 获取活跃流列表
  - [x] 11.2: 实现启动/停止视频流的 UI 操作
  - [x] 11.3: 修改 Dashboard.tsx 中的视频矩阵，使用活跃流数据

- [x] Task 12: 前端设置页面对接系统状态 API
  - [x] 12.1: 修改 SettingsPage.tsx，从 `/api/system/status` 获取真实连接状态
  - [x] 12.2: 移除硬编码的"已连接"标签

- [x] Task 13: 前端登录页移除默认密码显示
  - [x] 13.1: 移除 LoginPage.tsx 中的默认管理员密码提示

- [x] Task 14: 实现 WebSocket 设备控制逻辑
  - [x] 14.1: 在 routes.py WebSocket 处理中实现 `device_control` 消息类型处理
  - [x] 14.2: 实现 `device_status_query` 消息类型，返回设备当前状态

- [x] Task 15: 实现 DeviceSearchFilter 设备类型筛选
  - [x] 15.1: 在 DeviceSearchFilter.tsx 中添加设备类型下拉筛选
  - [x] 15.2: 支持按 manhole_cover / camera 等类型筛选

# Task Dependencies
- Task 2 依赖 Task 1（需要固定 UUID 才能正确关联用户）
- Task 4 和 Task 5 有依赖关系（5 依赖 4）
- Task 6 依赖 Task 5（推理结果需要通过 InfluxDB 持久化）
- Task 7 依赖 Task 5（Redis 广播需要 MockDataGenerator 改造完成）
- Task 8 可与 Task 4-7 并行
- Task 9-15 依赖对应后端 Task 完成
- Task 10 依赖 Task 5（图表需要 InfluxDB 数据）
- Task 11 依赖 Task 6（视频监控需要推理集成）
- Task 12 依赖 Task 8（设置页面需要系统状态 API）
