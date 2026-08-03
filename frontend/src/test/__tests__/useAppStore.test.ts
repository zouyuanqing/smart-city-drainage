import { describe, it, expect, beforeEach } from 'vitest'
import { useAppStore } from '../../store/useAppStore'
import type { Alert } from '../../types'

const makeAlert = (id: string): Alert => ({
  id,
  alert_type: 'water_accumulation',
  level: 'warning',
  title: `告警 ${id}`,
  description: '测试告警',
  is_acknowledged: false,
  is_resolved: false,
  created_at: '2026-01-01T00:00:00Z',
})

describe('useAppStore', () => {
  beforeEach(() => {
    useAppStore.setState({
      currentUser: null,
      sensorReadings: new Map(),
      readingHistory: new Map(),
      alerts: [],
      devices: [],
      selectedDeviceId: null,
      selectedAlertId: null,
      sidebarCollapsed: false,
      isVideoPanelOpen: false,
    })
  })

  it('should initialize with default values', () => {
    const state = useAppStore.getState()
    expect(state.currentUser).toBeNull()
    expect(state.devices).toEqual([])
    expect(state.alerts).toEqual([])
    expect(state.sidebarCollapsed).toBe(false)
  })

  it('should set current user', () => {
    useAppStore
      .getState()
      .setCurrentUser({ id: '1', username: 'admin', email: 'admin@example.com', role: 'admin' })
    const state = useAppStore.getState()
    expect(state.currentUser?.username).toBe('admin')
    expect(state.currentUser?.role).toBe('admin')
  })

  it('should add, acknowledge and resolve alerts', () => {
    useAppStore.getState().addAlert(makeAlert('a1'))
    useAppStore.getState().addAlert(makeAlert('a2'))
    expect(useAppStore.getState().alerts).toHaveLength(2)
    expect(useAppStore.getState().unacknowledgedCount()).toBe(2)

    useAppStore.getState().acknowledgeAlert('a1')
    expect(useAppStore.getState().alerts[1].is_acknowledged).toBe(true)
    expect(useAppStore.getState().unacknowledgedCount()).toBe(1)

    useAppStore.getState().resolveAlert('a2')
    expect(useAppStore.getState().alerts[0].is_resolved).toBe(true)
  })

  it('should set devices', () => {
    useAppStore.getState().setDevices([
      {
        id: 'd1',
        code: 'MH-001',
        name: '井盖 1',
        device_type: 'manhole_cover',
        status: 'online',
        lat: 31.23,
        lng: 121.47,
        battery_level: 80,
        signal_strength: 3,
      },
    ])
    expect(useAppStore.getState().devices).toHaveLength(1)
    expect(useAppStore.getState().devices[0].code).toBe('MH-001')
  })

  it('should keep per-device reading history capped at 30', () => {
    const store = useAppStore.getState()
    for (let i = 0; i < 35; i++) {
      store.setSensorReadings([
        {
          device_id: 'd1',
          device_code: 'MH-001',
          device_name: '井盖 1',
          water_level_mm: i,
          flow_rate_m3h: 0,
          temperature_c: 20,
          battery_level: 80,
          signal_strength: 3,
          timestamp: `2026-01-01T00:00:${String(i).padStart(2, '0')}Z`,
        },
      ])
    }
    const history = useAppStore.getState().getReadingHistory('d1')
    expect(history).toHaveLength(30)
    expect(history[29].water_level_mm).toBe(34)
  })

  it('should toggle UI state', () => {
    useAppStore.getState().toggleSidebar()
    expect(useAppStore.getState().sidebarCollapsed).toBe(true)
    useAppStore.getState().setSelectedDevice('d1')
    expect(useAppStore.getState().selectedDeviceId).toBe('d1')
  })
})
