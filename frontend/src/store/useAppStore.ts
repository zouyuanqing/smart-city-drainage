/**
 * 全局 Zustand 状态管理
 * 管理传感器数据、告警、设备状态、UI 状态。
 */

import { create } from 'zustand';
import type { SensorReading, Alert, Device } from '@/types';

export type UserRole = 'admin' | 'operator' | 'viewer';

export interface User {
  id: string;
  username: string;
  email: string;
  full_name?: string;
  role: UserRole;
}

const ROLE_HIERARCHY: Record<UserRole, number> = {
  admin: 3,
  operator: 2,
  viewer: 1,
};

export function hasRole(requiredRole: UserRole): boolean {
  const stored = localStorage.getItem('scn_user_role');
  const userRole = (stored || 'viewer') as UserRole;
  return ROLE_HIERARCHY[userRole] >= ROLE_HIERARCHY[requiredRole];
}

export function getUserRole(): UserRole {
  const stored = localStorage.getItem('scn_user_role');
  return (stored || 'viewer') as UserRole;
}

interface AppState {
  // ---- 用户 ----
  currentUser: User | null;
  setCurrentUser: (user: User | null) => void;

  // ---- 传感器数据 ----
  sensorReadings: Map<string, SensorReading>;
  readingHistory: Map<string, SensorReading[]>;  // 每设备保留最近 30 条
  setSensorReadings: (readings: SensorReading[]) => void;
  getLatestReading: (deviceId: string) => SensorReading | undefined;
  getReadingHistory: (deviceId: string) => SensorReading[];

  // ---- 告警 ----
  alerts: Alert[];
  addAlert: (alert: Alert) => void;
  acknowledgeAlert: (alertId: string) => void;
  resolveAlert: (alertId: string) => void;
  unacknowledgedCount: () => number;

  // ---- 设备 ----
  devices: Device[];
  setDevices: (devices: Device[]) => void;

  // ---- UI 状态 ----
  selectedDeviceId: string | null;
  setSelectedDevice: (id: string | null) => void;
  selectedAlertId: string | null;
  setSelectedAlert: (id: string | null) => void;
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  isVideoPanelOpen: boolean;
  toggleVideoPanel: () => void;
}

export const useAppStore = create<AppState>((set, get) => ({
  // ---- 用户 ----
  currentUser: null,
  setCurrentUser: (user) => set({ currentUser: user }),

  // ---- 传感器数据 ----
  sensorReadings: new Map(),
  readingHistory: new Map(),

  setSensorReadings: (readings) => {
    const newMap = new Map(get().sensorReadings);
    const newHistory = new Map(get().readingHistory);
    for (const r of readings) {
      newMap.set(r.device_id, r);
      const hist = newHistory.get(r.device_id) || [];
      hist.push(r);
      if (hist.length > 30) hist.shift();
      newHistory.set(r.device_id, hist);
    }
    set({ sensorReadings: newMap, readingHistory: newHistory });
  },

  getLatestReading: (deviceId) => {
    return get().sensorReadings.get(deviceId);
  },

  getReadingHistory: (deviceId) => {
    return get().readingHistory.get(deviceId) || [];
  },

  // ---- 告警 ----
  alerts: [],

  addAlert: (alert) => {
    set((state) => {
      // 去重
      if (state.alerts.some((a) => a.id === alert.id)) {
        return state;
      }
      const newAlerts = [alert, ...state.alerts].slice(0, 200);
      return { alerts: newAlerts };
    });
  },

  acknowledgeAlert: (alertId) => {
    set((state) => ({
      alerts: state.alerts.map((a) =>
        a.id === alertId ? { ...a, is_acknowledged: true } : a,
      ),
    }));
  },

  resolveAlert: (alertId) => {
    set((state) => ({
      alerts: state.alerts.map((a) =>
        a.id === alertId ? { ...a, is_acknowledged: true, is_resolved: true } : a,
      ),
    }));
  },

  unacknowledgedCount: () => {
    return get().alerts.filter((a) => !a.is_acknowledged).length;
  },

  // ---- 设备 ----
  devices: [],
  setDevices: (devices) => set({ devices }),

  // ---- UI 状态 ----
  selectedDeviceId: null,
  setSelectedDevice: (id) => set({ selectedDeviceId: id }),
  selectedAlertId: null,
  setSelectedAlert: (id) => set({ selectedAlertId: id }),
  sidebarCollapsed: false,
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
  isVideoPanelOpen: false,
  toggleVideoPanel: () => set((s) => ({ isVideoPanelOpen: !s.isVideoPanelOpen })),
}));
