/**
 * 全局类型定义
 */

// ---------- 设备 ----------
export interface Device {
  id: string
  code: string
  name: string
  device_type: string
  status: 'online' | 'offline' | 'fault' | 'maintenance'
  lat: number
  lng: number
  district?: string
  battery_level: number
  signal_strength: number
}

// ---------- 传感器读数 ----------
export interface SensorReading {
  device_id: string
  device_code: string
  device_name: string
  water_level_mm: number
  flow_rate_m3h: number
  water_quality_ph?: number
  temperature_c: number
  humidity_pct?: number
  battery_level: number
  signal_strength: number
  timestamp: string
}

// ---------- 告警 ----------
export type AlertLevel = 'critical' | 'warning' | 'info'
export type AlertType =
  | 'water_accumulation'
  | 'manhole_anomaly'
  | 'intrusion'
  | 'illegal_parking'
  | 'water_level_high'
  | 'flow_anomaly'
  | 'device_offline'
  | 'system_error'

export interface Alert {
  id: string
  alert_type: AlertType
  level: AlertLevel
  title: string
  description?: string
  device_id?: string
  device_name?: string
  latitude?: number
  longitude?: number
  snapshot_url?: string
  bbox_coordinates?: number[]
  detection_confidence?: number
  is_acknowledged: boolean
  is_resolved: boolean
  created_at: string
}

// ---------- SSE 事件 ----------
export interface SSEEvent {
  event: string
  data: string
}

export interface SSESensorPayload {
  readings: SensorReading[]
  timestamp: string
}

export interface SSEAlertPayload {
  alert: Alert
  timestamp: string
}

// ---------- 推理 ----------
export interface Detection {
  class_id: number
  class_name: string
  confidence: number
  bbox: number[]
}

export interface InferenceResult {
  detections: Detection[]
  inference_time_ms: number
  model_version: string
  image_width?: number
  image_height?: number
  annotated_image_base64?: string
}

// ---------- 模型 ----------
export interface ModelStatus {
  active_version: string
  is_ready: boolean
  device: string
  registry: Record<
    string,
    {
      status: string
      model_type: string
      file_size_mb: number
      inference_count: number
      avg_inference_ms: number
    }
  >
}

// ---------- 视频流 ----------
export interface StreamInfo {
  camera_id: string
  name?: string
  is_active: boolean
  is_healthy: boolean
  uptime_seconds: number
  restart_count: number
  hls_playlist?: string
  error?: string
}

// ---------- 历史数据 ----------
export interface HistoricalDataPoint {
  time: string
  water_level_mm: number
  flow_rate_m3h: number
}
