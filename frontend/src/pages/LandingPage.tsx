/**
 * 落地页 — 全屏 3D 交互式城市地图首屏
 * Dark Cyberpunk / Sci-Fi Command Center 风格
 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Button, Tag } from 'antd'
import {
  ThunderboltOutlined,
  AimOutlined,
  DashboardOutlined,
  SafetyCertificateOutlined,
  EyeOutlined,
  ArrowRightOutlined,
} from '@ant-design/icons'
import { deviceAPI, alertAPI } from '@/services/api'

// 脉冲数据流粒子
function DataParticles() {
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      {Array.from({ length: 30 }).map((_, i) => (
        <motion.div
          key={i}
          className="absolute w-1 h-1 rounded-full"
          style={{
            background: i % 3 === 0 ? '#00D4FF' : i % 3 === 1 ? '#00FF88' : '#FF3366',
            left: `${Math.random() * 100}%`,
            top: `${Math.random() * 100}%`,
          }}
          animate={{
            opacity: [0, 0.8, 0],
            scale: [0, 1.5, 0],
          }}
          transition={{
            duration: 2 + Math.random() * 3,
            repeat: Infinity,
            delay: Math.random() * 5,
            ease: 'easeInOut',
          }}
        />
      ))}
    </div>
  )
}

// 旋转装饰环
function RotatingRings() {
  return (
    <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-10">
      <motion.div
        className="absolute w-[600px] h-[600px] border border-neon-blue rounded-full"
        animate={{ rotate: 360, scale: [1, 1.1, 1] }}
        transition={{ duration: 30, repeat: Infinity, ease: 'linear' }}
      />
      <motion.div
        className="absolute w-[450px] h-[450px] border border-neon-purple rounded-full"
        animate={{ rotate: -360, scale: [1, 0.9, 1] }}
        transition={{ duration: 25, repeat: Infinity, ease: 'linear' }}
      />
      <motion.div
        className="absolute w-[300px] h-[300px] border border-neon-cyan rounded-full"
        animate={{ rotate: 180, scale: [1, 1.05, 1] }}
        transition={{ duration: 20, repeat: Infinity, ease: 'linear' }}
      />
    </div>
  )
}

// 统计卡片
function StatCard({
  label,
  value,
  unit,
  icon,
  color,
}: {
  label: string
  value: string
  unit?: string
  icon: React.ReactNode
  color: string
}) {
  return (
    <motion.div
      className="relative bg-cyber-dark/80 backdrop-blur-md border border-cyber-border rounded-lg p-4
                 hover:border-opacity-60 transition-all duration-300 group cursor-default"
      whileHover={{ scale: 1.03, y: -2 }}
    >
      <div className="absolute inset-0 bg-gradient-to-br from-transparent to-neon-blue/5 rounded-lg" />
      <div className="relative z-10">
        <div className="flex items-center gap-2 mb-2">
          <span style={{ color }}>{icon}</span>
          <span className="text-text-secondary text-xs font-mono uppercase tracking-wider">
            {label}
          </span>
        </div>
        <div className="flex items-baseline gap-1">
          <span className="data-value" style={{ color, textShadow: `0 0 10px ${color}40` }}>
            {value}
          </span>
          {unit && <span className="data-unit">{unit}</span>}
        </div>
      </div>
    </motion.div>
  )
}

// 地图点位示意
function MapPreview() {
  const points = [
    { x: 28, y: 40, name: '中山路', pulse: 'green' },
    { x: 52, y: 35, name: '南京路', pulse: 'blue' },
    { x: 68, y: 55, name: '陆家嘴', pulse: 'green' },
    { x: 35, y: 62, name: '徐家汇', pulse: 'red' },
    { x: 75, y: 28, name: '五角场', pulse: 'green' },
  ]

  return (
    <div className="relative w-full h-full cyber-grid rounded-lg overflow-hidden border border-cyber-border">
      {/* 模拟道路网格 */}
      <svg className="absolute inset-0 w-full h-full opacity-20" viewBox="0 0 100 100">
        <line x1="20" y1="0" x2="20" y2="100" stroke="#00D4FF" strokeWidth="0.3" />
        <line x1="45" y1="0" x2="45" y2="100" stroke="#00D4FF" strokeWidth="0.2" />
        <line x1="70" y1="0" x2="70" y2="100" stroke="#00D4FF" strokeWidth="0.3" />
        <line x1="0" y1="30" x2="100" y2="30" stroke="#00D4FF" strokeWidth="0.2" />
        <line x1="0" y1="55" x2="100" y2="55" stroke="#00D4FF" strokeWidth="0.3" />
        <line x1="0" y1="78" x2="100" y2="78" stroke="#00D4FF" strokeWidth="0.2" />
      </svg>

      {/* 脉冲点位 */}
      {points.map((p, i) => (
        <motion.div
          key={i}
          className="absolute pulse-dot cursor-pointer"
          style={{
            left: `${p.x}%`,
            top: `${p.y}%`,
            width: 16,
            height: 16,
            color: p.pulse === 'red' ? '#FF3366' : p.pulse === 'blue' ? '#00D4FF' : '#00FF88',
          }}
          whileHover={{ scale: 1.5 }}
        >
          <div
            className="absolute -top-6 left-1/2 -translate-x-1/2 whitespace-nowrap
                          text-[10px] font-mono text-neon-blue opacity-0 hover:opacity-100 transition-opacity"
          >
            {p.name}
          </div>
        </motion.div>
      ))}

      {/* HUD 扫描环 */}
      <motion.div
        className="absolute border border-neon-blue/30 rounded-full"
        style={{
          width: 80,
          height: 80,
          left: '50%',
          top: '50%',
          marginLeft: -40,
          marginTop: -40,
        }}
        animate={{ scale: [1, 2, 1], opacity: [0.5, 0, 0.5] }}
        transition={{ duration: 4, repeat: Infinity, ease: 'easeInOut' }}
      />
    </div>
  )
}

export function LandingPage() {
  const navigate = useNavigate()
  const [stats, setStats] = useState({ devices: 8, online: 6, alerts: 3, coverage: '98.5' })

  useEffect(() => {
    Promise.all([
      deviceAPI.getList().catch(() => ({ data: { devices: [] } })),
      alertAPI.getList(100).catch(() => ({ data: { alerts: [] } })),
    ]).then(([devRes, alertRes]) => {
      const devices = devRes.data.devices || []
      const alerts = alertRes.data.alerts || []
      setStats({
        devices: devices.length,
        online: devices.filter((d: any) => d.status === 'online').length,
        alerts: alerts.filter((a: any) => !a.is_acknowledged).length,
        coverage:
          devices.length > 0
            ? (
                (devices.filter((d: any) => d.status === 'online').length / devices.length) *
                100
              ).toFixed(1)
            : '0.0',
      })
    })
  }, [])

  return (
    <div className="relative min-h-screen bg-cyber-black overflow-hidden">
      {/* 背景效果 */}
      <div className="absolute inset-0 cyber-grid opacity-50" />
      <DataParticles />
      <RotatingRings />

      {/* 顶部导航 */}
      <header
        className="relative z-20 flex items-center justify-between px-8 py-4
                         border-b border-cyber-border bg-cyber-dark/60 backdrop-blur-md"
      >
        <div className="flex items-center gap-3">
          <motion.div
            className="w-8 h-8 rounded-full bg-gradient-to-br from-neon-blue to-neon-cyan"
            animate={{ boxShadow: ['0 0 10px #00D4FF', '0 0 25px #00D4FF', '0 0 10px #00D4FF'] }}
            transition={{ duration: 2, repeat: Infinity }}
          />
          <div>
            <h1 className="font-display text-lg font-bold neon-text tracking-wider">
              SCN ENDPOINTS
            </h1>
            <p className="text-[10px] text-text-muted font-mono uppercase tracking-widest">
              Smart City Neural Network
            </p>
          </div>
        </div>

        <nav className="flex items-center gap-6">
          <Button
            type="text"
            className="text-text-secondary hover:text-neon-blue font-mono text-xs"
            onClick={() => navigate('/login')}
          >
            登录
          </Button>
          <Button
            type="text"
            className="text-text-secondary hover:text-neon-blue font-mono text-xs"
            icon={<DashboardOutlined />}
            onClick={() => navigate('/dashboard')}
          >
            驾驶舱
          </Button>
          <Button
            type="primary"
            icon={<ArrowRightOutlined />}
            onClick={() => navigate('/dashboard')}
            className="font-mono text-xs tracking-wider"
          >
            进入系统
          </Button>
        </nav>
      </header>

      {/* 主内容 */}
      <main className="relative z-10">
        {/* 英雄区域 */}
        <section className="relative px-8 pt-16 pb-12">
          <div className="max-w-7xl mx-auto grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
            {/* 左侧文字 */}
            <motion.div
              initial={{ opacity: 0, x: -50 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.8 }}
            >
              <Tag color="cyan" className="mb-4 font-mono text-[10px] tracking-wider">
                AI-POWERED MONITORING SYSTEM
              </Tag>
              <h2 className="text-4xl lg:text-5xl font-display font-bold leading-tight mb-4">
                <span className="text-white">智慧城市</span>
                <br />
                <span className="neon-text">神经末梢</span>
              </h2>
              <p className="text-text-secondary text-lg mb-8 leading-relaxed max-w-lg">
                基于 AI 计算机视觉与物联网技术，构建城市排水系统的数字孪生。
                实时监测液位、流量、井盖状态，为城市安全运行提供决策支撑。
              </p>

              <div className="flex gap-4 mb-8">
                <Button
                  type="primary"
                  size="large"
                  icon={<DashboardOutlined />}
                  onClick={() => navigate('/dashboard')}
                  className="h-12 px-8 font-mono text-sm tracking-wider"
                >
                  进入驾驶舱
                </Button>
                <Button
                  size="large"
                  ghost
                  icon={<EyeOutlined />}
                  className="h-12 font-mono text-sm tracking-wider border-neon-blue text-neon-blue"
                >
                  查看演示
                </Button>
              </div>

              {/* 特性标签 */}
              <div className="flex flex-wrap gap-3">
                {[
                  'YOLOv8 AI 视觉',
                  'RTSP/HLS 多源视频',
                  '3D 数字孪生',
                  '零停机模型热切换',
                  '毫秒级实时推送',
                ].map((t) => (
                  <Tag
                    key={t}
                    className="bg-cyber-dark border-cyber-border text-text-secondary font-mono text-[10px] py-1 px-3"
                  >
                    {t}
                  </Tag>
                ))}
              </div>
            </motion.div>

            {/* 右侧地图预览 */}
            <motion.div
              className="h-[480px]"
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ duration: 1, delay: 0.3 }}
            >
              <MapPreview />
            </motion.div>
          </div>
        </section>

        {/* 统计条 */}
        <section className="px-8 pb-16">
          <div className="max-w-7xl mx-auto">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatCard
                label="监控设备"
                value={String(stats.devices)}
                unit="台"
                icon={<AimOutlined />}
                color="#00D4FF"
              />
              <StatCard
                label="在线率"
                value={stats.coverage}
                unit="%"
                icon={<ThunderboltOutlined />}
                color="#00FF88"
              />
              <StatCard
                label="今日告警"
                value={String(stats.alerts)}
                unit="条"
                icon={<SafetyCertificateOutlined />}
                color="#FF3366"
              />
              <StatCard
                label="AI 模型"
                value="v1"
                unit="active"
                icon={<DashboardOutlined />}
                color="#A855F7"
              />
            </div>
          </div>
        </section>

        {/* 底部数据河流装饰 */}
        <div className="relative h-32 overflow-hidden border-t border-cyber-border">
          <div className="data-river h-full" />
          <div className="absolute bottom-0 left-0 right-0 flex justify-center pb-4">
            <p className="text-text-muted text-xs font-mono tracking-widest">
              ⚡ SMART CITY NEURAL ENDPOINTS — 城市排水智能监测系统 ⚡
            </p>
          </div>
        </div>
      </main>
    </div>
  )
}
