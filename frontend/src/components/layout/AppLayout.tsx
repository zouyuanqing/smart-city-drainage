/**
 * 驾驶舱整体布局 — 顶部状态栏 + 侧边栏 + 主内容区
 */

import { useState, useEffect } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Layout, Button, Badge, Tag, Tooltip } from 'antd';
import {
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  DashboardOutlined,
  EnvironmentOutlined,
  VideoCameraOutlined,
  AlertOutlined,
  SettingOutlined,
  WifiOutlined,
  ApiOutlined,
  ExperimentOutlined,
  LogoutOutlined,
} from '@ant-design/icons';
import { motion } from 'framer-motion';
import { useAppStore } from '@/store/useAppStore';
import { modelAPI } from '@/services/api';
import type { ModelStatus } from '@/types';

const { Header, Sider, Content } = Layout;

interface AppLayoutProps {
  children: React.ReactNode;
}

export function AppLayout({ children }: AppLayoutProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { sidebarCollapsed, toggleSidebar, alerts } = useAppStore();
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [currentTime, setCurrentTime] = useState('');
  const unackCount = alerts.filter(a => !a.is_acknowledged).length;

  useEffect(() => {
    modelAPI.getStatus().then(res => setModelStatus(res.data)).catch(() => {});
    const timer = setInterval(() => {
      setCurrentTime(new Date().toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      }));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const handleLogout = () => {
    localStorage.removeItem('scn_access_token');
    localStorage.removeItem('scn_user');
    navigate('/login', { replace: true });
  };

  const currentPath = location.pathname;

  const menuItems = [
    { key: 'dashboard', path: '/dashboard', icon: <DashboardOutlined />, label: '驾驶舱总览' },
    { key: 'map', path: '/map', icon: <EnvironmentOutlined />, label: 'GIS 地图' },
    { key: 'video', path: '/video', icon: <VideoCameraOutlined />, label: '视频监控' },
    { key: 'alerts', path: '/alerts', icon: <AlertOutlined />, label: `告警中心 ${unackCount > 0 ? `(${unackCount})` : ''}` },
    { key: 'settings', path: '/settings', icon: <SettingOutlined />, label: '系统设置' },
  ];

  return (
    <Layout className="min-h-screen bg-cyber-black">
      {/* 顶部状态栏 */}
      <Header className="flex items-center justify-between px-4 h-12 bg-cyber-dark border-b border-cyber-border"
              style={{ padding: '0 16px', lineHeight: '48px' }}>
        <div className="flex items-center gap-3">
          <Button type="text" icon={sidebarCollapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
                  onClick={toggleSidebar} className="text-neon-blue" />

          <div className="h-6 w-px bg-cyber-border mx-2" />

          <span className="font-display text-sm font-bold neon-text tracking-wider">
            SCN | 智慧排水指挥中心
          </span>
        </div>

        <div className="flex items-center gap-4">
          {/* 系统状态指示器 */}
          <Tooltip title="SSE 连接状态">
            <Badge status="processing" color="cyan" text={<span className="text-text-secondary text-xs font-mono">LIVE</span>} />
          </Tooltip>

          <Tooltip title={`AI 模型: ${modelStatus?.active_version || '--'}`}>
            <Tag color={modelStatus?.is_ready ? 'green' : 'red'} className="font-mono text-[10px]">
              <ApiOutlined /> {modelStatus?.active_version || 'N/A'}
            </Tag>
          </Tooltip>

          <Tooltip title="WebSocket 控制通道">
            <WifiOutlined className="text-neon-green text-sm" />
          </Tooltip>

          <span className="text-text-secondary font-mono text-xs">{currentTime}</span>

          <Tag color="purple" className="font-mono text-[10px]">
            <ExperimentOutlined /> v1.0.0
          </Tag>
        </div>
      </Header>

      <Layout>
        {/* 侧边栏 */}
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
              const isActive = currentPath === item.path;
              return (
                <motion.div
                  key={item.key}
                  className={`flex items-center gap-3 px-4 py-3 cursor-pointer mx-2 rounded
                              transition-all duration-200 group
                              ${isActive ? 'bg-neon-blue/10 border-l-2 border-neon-blue' : 'hover:bg-cyber-medium'}`}
                  whileHover={{ x: 4 }}
                  onClick={() => navigate(item.path)}
                >
                  <span className={`text-lg ${item.key === 'alerts' && unackCount > 0 ? 'text-neon-red animate-pulse-glow' : isActive ? 'text-neon-blue' : 'text-text-secondary group-hover:text-neon-blue'}`}>
                    {item.icon}
                  </span>
                  {!sidebarCollapsed && (
                    <span className={`text-sm font-mono tracking-wide ${isActive ? 'text-neon-blue' : 'text-text-secondary group-hover:text-neon-blue'}`}>
                      {item.label}
                    </span>
                  )}
                </motion.div>
              );
            })}

            {/* 退出登录 */}
            <div className="mt-auto pt-3 border-t border-cyber-border mx-2">
              <motion.div
                className="flex items-center gap-3 px-4 py-3 cursor-pointer rounded
                            hover:bg-cyber-medium transition-all duration-200 group"
                whileHover={{ x: 4 }}
                onClick={handleLogout}
              >
                <LogoutOutlined className="text-text-muted group-hover:text-neon-red text-lg" />
                {!sidebarCollapsed && (
                  <span className="text-text-muted text-sm font-mono tracking-wide group-hover:text-neon-red">
                    退出登录
                  </span>
                )}
              </motion.div>
            </div>
          </div>
        </Sider>

        {/* 主内容区 */}
        <Content className="p-2 lg:p-4 bg-cyber-black overflow-auto" style={{ minHeight: 'calc(100vh - 48px)' }}>
          {children}
          <div className="text-center py-2 text-text-muted text-[10px] font-mono tracking-wider opacity-50">
            Powered by Smart City Neural Endpoints
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}
