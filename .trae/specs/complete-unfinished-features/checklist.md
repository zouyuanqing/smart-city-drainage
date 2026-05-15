# Checklist

## Phase 1: 后端核心 Bug 修复
- [x] MockDataGenerator `self.interval` Bug 已修复为 `self.sensor_interval`
- [x] PRESET_DEVICES 使用固定 UUID，与 init-db.sql 中的设备 ID 一致
- [x] `/api/auth/login` 从数据库查询用户，硬编码 ADMIN_USER 已移除
- [x] 登录失败返回正确 401 错误
- [x] `/api/alerts` 数据库为空时返回空列表，不再回退到模拟数据
- [x] `/api/alerts/{alert_id}/acknowledge` 数据库操作失败时返回 500 错误
- [x] `/api/devices` 数据库为空时返回空列表，不再回退到预设数据

## Phase 2: InfluxDB 集成
- [x] `services/influxdb_service.py` 已创建，包含完整的读写方法
- [x] InfluxDB 客户端在 main.py lifespan 中正确初始化
- [x] `/api/sensors/latest` 优先从 InfluxDB 查询，降级时标注 `"source": "mock"`
- [x] `/api/sensors/history/{device_id}` 优先从 InfluxDB 查询
- [x] MockDataGenerator 生成数据时同步写入 InfluxDB

## Phase 3: AI 推理管线集成
- [x] stream_service.start_stream() 自动创建关联 InferenceWorker
- [x] stream_service.stop_stream() 同时移除 InferenceWorker
- [x] `/api/streams/status` 响应包含推理 Worker 状态
- [x] 新增推理启动/停止 API 端点

## Phase 4: Redis 消息总线
- [x] Redis 频道处理器在 main.py 中正确注册
- [x] Redis 消息接收后通过 SSEManager 正确广播
- [x] MockDataGenerator 通过 redis_client.publish 发布数据
- [x] InferenceWorker 告警通过 redis_client.publish_alert 发布

## Phase 5: 系统状态 API
- [x] `services/system_status.py` 已创建
- [x] `GET /api/system/status` 返回各组件真实连接状态
- [x] 前端 api.ts 中已添加 systemAPI.getStatus()

## Phase 6: 前端修复
- [x] Dashboard.tsx 使用后端返回的真实设备状态值
- [x] MapView.tsx 使用后端返回的真实设备状态值
- [x] LandingPage.tsx 统计数据从 API 计算得出
- [x] WaterLevelChart.tsx 通过 SSE 接收实时数据，移除 Math.random()
- [x] FlowTrendChart.tsx 通过 SSE 接收实时数据，移除 Math.random()
- [x] VideoMonitor.tsx 从流管理 API 获取活跃流列表
- [x] Dashboard.tsx 视频矩阵使用活跃流数据
- [x] SettingsPage.tsx 从系统状态 API 获取真实连接状态
- [x] LoginPage.tsx 默认密码提示已移除
- [x] WebSocket 实现了 device_control 消息处理
- [x] DeviceSearchFilter 支持设备类型筛选
