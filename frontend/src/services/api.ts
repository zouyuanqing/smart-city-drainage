/**
 * API 服务层
 * 封装所有后端 API 调用，使用 axios。
 */

import axios, { AxiosInstance, AxiosResponse } from 'axios';
import type {
  Alert,
  InferenceResult,
  ModelStatus,
  SensorReading,
  StreamInfo,
} from '@/types';

const BASE_URL = '/api';

/** 创建 axios 实例 */
const api: AxiosInstance = axios.create({
  baseURL: BASE_URL,
  timeout: 30000,
  headers: { 'Content-Type': 'application/json' },
});

// ========== 请求拦截器 (JWT) ==========
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('scn_access_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// ========== 响应拦截器 ==========
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const msg = error.response?.data?.detail || error.message || '网络请求失败';
    if (error.response?.status === 401) {
      localStorage.removeItem('scn_access_token');
      localStorage.removeItem('scn_user');
      localStorage.removeItem('scn_user_role');
      window.location.href = '/login';
    }
    console.error(`[API Error] ${error.config?.url}:`, msg);
    return Promise.reject(new Error(msg));
  },
);

// ============================
// 认证
// ============================
export const authAPI = {
  login: (username: string, password: string) =>
    api.post('/auth/login', { username, password }),
};

// ============================
// 传感器
// ============================
export const sensorAPI = {
  getLatest: () =>
    api.get<{ readings: SensorReading[] }>('/sensors/latest'),

  getHistory: (deviceId: string, hours = 24, intervalMinutes = 5) =>
    api.get<{ data: { time: string; water_level_mm: number; flow_rate_m3h: number }[] }>(
      `/sensors/history/${deviceId}`,
      { params: { hours, interval_minutes: intervalMinutes } },
    ),
};

// ============================
// 告警
// ============================
export const alertAPI = {
  getList: (limit = 50, level?: string) =>
    api.get<{ alerts: Alert[] }>('/alerts', { params: { limit, level } }),

  acknowledge: (alertId: string, action: string) =>
    api.post(`/alerts/${alertId}/acknowledge`, { action }),
};

// ============================
// 设备
// ============================
export const deviceAPI = {
  getList: () =>
    api.get<{ devices: { id: string; code: string; name: string; lat: number; lng: number; district: string }[] }>('/devices'),
};

// ============================
// 模型管理
// ============================
export const modelAPI = {
  getStatus: () =>
    api.get<ModelStatus>('/models/status'),

  switchModel: (targetVersion: string, verify = true) =>
    api.post('/models/switch', { target_version: targetVersion, verify }),

  getVersions: () =>
    api.get('/models/versions'),
};

// ============================
// 推理
// ============================
export const inferenceAPI = {
  detectFromBase64: (imageBase64: string, confidence = 0.45) =>
    api.post<InferenceResult>('/inference/detect', {
      image_base64: imageBase64,
      confidence_threshold: confidence,
      return_annotated: true,
    }),

  detectFromUrl: (imageUrl: string, confidence = 0.45) =>
    api.post<InferenceResult>('/inference/detect', {
      image_url: imageUrl,
      confidence_threshold: confidence,
      return_annotated: true,
    }),
};

// ============================
// 视频流
// ============================
export const streamAPI = {
  start: (params: { camera_id: string; rtsp_url: string }) =>
    api.post('/streams/start', {
      device_id: params.camera_id,
      stream_url: params.rtsp_url,
      name: `stream_${params.camera_id}`,
      protocol: 'rtsp',
    }),

  stop: (cameraId: string) =>
    api.post(`/streams/${cameraId}/stop`),

  getStatus: () =>
    api.get<{ streams: Record<string, StreamInfo> }>('/streams/status'),
};

// ============================
// 模拟数据控制
// ============================
export interface MockConfig {
  sensor_interval: number;
  alert_interval_seconds: number;
  alert_probability: number;
  alert_count_per_batch: number;
}

export interface SystemStatus {
  postgresql: { status: string; detail: string };
  influxdb: { status: string; detail: string };
  redis: { status: string; detail: string };
  model: { status: string; active_version: string; device: string };
  timestamp: string;
}

export const systemAPI = {
  getStatus: () =>
    api.get<SystemStatus>('/system/status'),
};

export const mockAPI = {
  getStatus: () => api.get<{ running: boolean }>('/mock/status'),
  getConfig: () => api.get<MockConfig>('/mock/config'),
  updateConfig: (config: Partial<MockConfig>) => api.put('/mock/config', config),
  start: () => api.post('/mock/start'),
  stop: () => api.post('/mock/stop'),
};

// ============================
// 数据导出
// ============================
export const exportAPI = {
  sensorData: async (params?: { device_id?: string; start_time?: string; end_time?: string }) => {
    const query = new URLSearchParams(params as Record<string, string>).toString()
    const response = await api.get(`/sensors/export?${query}`, { responseType: 'blob' })
    return response
  },
  alertData: async (params?: { level?: string; start_time?: string; end_time?: string }) => {
    const query = new URLSearchParams(params as Record<string, string>).toString()
    const response = await api.get(`/alerts/export?${query}`, { responseType: 'blob' })
    return response
  },
};

export default api;
