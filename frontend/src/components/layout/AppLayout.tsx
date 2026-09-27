/**
 * 驾驶舱整体布局 — 顶部状态栏 + 侧边栏 + 主内容区
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Badge, Button, Layout, Tag, Tooltip } from 'antd'
import {
  AlertOutlined,
  ApiOutlined,
  DashboardOutlined,
  EnvironmentOutlined,
  ExperimentOutlined,
  FundProjectionScreenOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SettingOutlined,
  VideoCameraOutlined,
  WifiOutlined,
} from '@ant-design/icons'
import { motion } from 'framer-motion'
import { useTranslation } from 'react-i18next'
import { useAppStore } from '@/store/useAppStore'
import { hasRequiredRole } from '@/auth'
import { modelAPI } from '@/services/api'
import type { ModelStatus } from '@/types'
import type { AppRole } from '@/auth'

const { Header, Sider, Content } = Layout

interface AppLayoutProps {
  children: React.ReactNode
}

interface MenuItem {
  key: string
  path: string
  icon: React.ReactNode
  label: string
  requiredRole: AppRole
}

export function AppLayout({ children }: AppLayoutProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const { t, i18n } = useTranslation()
  const { sidebarCollapsed, toggleSidebar, alerts } = useAppStore()
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null)
  const [currentTime, setCurrentTime] = useState('')
  const unackCount = alerts.filter((a) => !a.is_acknowledged).length
  const locale = i18n.language?.startsWith('zh') ? 'zh-CN' : 'en-US'

  useEffect(() => {
    modelAPI
      .getStatus()
      .then((res) => setModelStatus(res.data))
      .catch(() => {})
    const updateTime = () => setCurrentTime(new Date().toLocaleString(locale))
    updateTime()
    const timer = setInterval(updateTime, 1000)
    return () => clearInterval(timer)
  }, [locale])

  const handleLogout = useCallback(() => {
    localStorage.removeItem('scn_access_token')
    localStorage.removeItem('scn_user')
    localStorage.removeItem('scn_user_role')
    navigate('/login', { replace: true })
  }, [navigate])

  const allMenuItems = useMemo<MenuItem[]>(
    () => [
      {
        key: 'dashboard',
        path: '/dashboard',
        icon: <DashboardOutlined />,
        label: t('nav.dashboard'),
        requiredRole: 'viewer',
      },
      {
        key: 'map',
        path: '/map',
        icon: <EnvironmentOutlined />,
        label: t('nav.map'),
        requiredRole: 'viewer',
      },
      {
        key: 'simulation',
        path: '/simulation',
        icon: <FundProjectionScreenOutlined />,
        label: t('nav.simulation'),
        requiredRole: 'viewer',
      },
      {
        key: 'video',
        path: '/video',
        icon: <VideoCameraOutlined />,
        label: t('nav.video'),
        requiredRole: 'operator',
      },
      {
        key: 'alerts',
        path: '/alerts',
        icon: <AlertOutlined />,
        label: `${t('nav.alerts')} ${unackCount > 0 ? `(${unackCount})` : ''}`,
        requiredRole: 'operator',
      },
      {
        key: 'settings',
        path: '/settings',
        icon: <SettingOutlined />,
        label: t('nav.settings'),
        requiredRole: 'admin',
      },
    ],
    [t, unackCount]
  )

  const menuItems = allMenuItems.filter((item) => hasRequiredRole(item.requiredRole))
  const currentPath = location.pathname

  return (
    <Layout className="min-h-screen bg-cyber-black">
      <Header
        className="flex items-center justify-between px-4 h-12 bg-cyber-dark border-b border-cyber-border"
        style={{ padding: '0 16px', lineHeight: '48px' }}
      >
        <div className="flex items-center gap-3">
          <Button
            type="text"
            icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={toggleSidebar}
            className="text-neon-blue"
            aria-label={sidebarCollapsed ? t('nav.expand') : t('nav.collapse')}
          />
          <div className="h-6 w-px bg-cyber-border mx-2" />
          <span className="font-display text-sm font-bold neon-text tracking-wider">
            SCN | {t('app.subtitle')}
          </span>
        </div>

        <div className="flex items-center gap-4">
          <Tooltip title={t('common.realtimeStream')}>
            <Badge
              status="processing"
              color="cyan"
              text={<span className="text-text-secondary text-xs font-mono">LIVE</span>}
            />
          </Tooltip>
          <Tooltip title={`${t('dashboard.aiModel')}: ${modelStatus?.active_version || '--'}`}>
            <Tag color={modelStatus?.is_ready ? 'green' : 'red'} className="font-mono text-[10px]">
              <ApiOutlined /> {modelStatus?.active_version || 'N/A'}
            </Tag>
          </Tooltip>
          <Tooltip title={t('common.websocketControl')}>
            <WifiOutlined className="text-neon-green text-sm" />
          </Tooltip>
          <span className="text-text-secondary font-mono text-xs">{currentTime}</span>
          <Tag color="purple" className="font-mono text-[10px]">
            <ExperimentOutlined /> v1.0.0
          </Tag>
        </div>
      </Header>

      <Layout>
        <Sider
          trigger={null}
          collapsible
          collapsed={sidebarCollapsed}
          width={200}
          className="bg-cyber-dark border-r border-cyber-border"
          style={{ background: '#111827' }}
        >
          <div className="flex flex-col h-full py-3">
            {menuItems.map((item) => {
              const isActive = currentPath === item.path || currentPath.startsWith(`${item.path}/`)
              return (
                <motion.button
                  type="button"
                  key={item.key}
                  className={`flex items-center gap-3 px-4 py-3 cursor-pointer mx-2 rounded
                              transition-all duration-200 group text-left border-0
                              ${isActive ? 'bg-neon-blue/10 border-l-2 border-neon-blue' : 'hover:bg-cyber-medium'}`}
                  whileHover={{ x: 4 }}
                  onClick={() => navigate(item.path)}
                  aria-current={isActive ? 'page' : undefined}
                  data-testid={`nav-${item.key}`}
                >
                  <span
                    className={`text-lg ${item.key === 'alerts' && unackCount > 0 ? 'text-neon-red animate-pulse-glow' : isActive ? 'text-neon-blue' : 'text-text-secondary group-hover:text-neon-blue'}`}
                  >
                    {item.icon}
                  </span>
                  {!sidebarCollapsed && (
                    <span
                      className={`text-sm font-mono tracking-wide ${isActive ? 'text-neon-blue' : 'text-text-secondary group-hover:text-neon-blue'}`}
                    >
                      {item.label}
                    </span>
                  )}
                </motion.button>
              )
            })}

            <div className="mt-auto pt-3 border-t border-cyber-border mx-2">
              <motion.button
                type="button"
                className="flex items-center gap-3 px-4 py-3 cursor-pointer rounded
                            hover:bg-cyber-medium transition-all duration-200 group border-0 text-left w-[calc(100%-1rem)]"
                whileHover={{ x: 4 }}
                onClick={handleLogout}
                data-testid="nav-logout"
              >
                <LogoutOutlined className="text-text-muted group-hover:text-neon-red text-lg" />
                {!sidebarCollapsed && (
                  <span className="text-text-muted text-sm font-mono tracking-wide group-hover:text-neon-red">
                    {t('nav.logout')}
                  </span>
                )}
              </motion.button>
            </div>
          </div>
        </Sider>

        <Content
          className="p-2 lg:p-4 bg-cyber-black overflow-auto"
          style={{ minHeight: 'calc(100vh - 48px)' }}
        >
          {children}
          <div className="text-center py-2 text-text-muted text-[10px] font-mono tracking-wider opacity-50">
            {t('app.footer')}
          </div>
        </Content>
      </Layout>
    </Layout>
  )
}
