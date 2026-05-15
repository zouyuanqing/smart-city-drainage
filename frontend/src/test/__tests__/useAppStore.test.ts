import { describe, it, expect, beforeEach } from 'vitest'
import { useAppStore } from '../../store/useAppStore'

describe('useAppStore', () => {
  beforeEach(() => {
    useAppStore.setState({
      isAuthenticated: false,
      user: null,
      token: null,
      devices: [],
      alerts: [],
      sensorData: {},
      activeStreams: [],
      systemStatus: null,
      sidebarCollapsed: false,
      theme: 'dark',
    })
  })

  it('should initialize with default values', () => {
    const state = useAppStore.getState()
    expect(state.isAuthenticated).toBe(false)
    expect(state.user).toBeNull()
    expect(state.token).toBeNull()
  })

  it('should set auth state on login', () => {
    useAppStore.getState().setAuth({ token: 'test-token' }, { id: '1', username: 'admin', role: 'admin' })
    const state = useAppStore.getState()
    expect(state.isAuthenticated).toBe(true)
    expect(state.token).toBe('test-token')
    expect(state.user?.username).toBe('admin')
  })

  it('should clear auth state on logout', () => {
    useAppStore.getState().setAuth({ token: 'test-token' }, { id: '1', username: 'admin', role: 'admin' })
    useAppStore.getState().logout()
    const state = useAppStore.getState()
    expect(state.isAuthenticated).toBe(false)
    expect(state.token).toBeNull()
  })
})
