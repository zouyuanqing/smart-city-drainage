/**
 * 登录页 — 身份认证
 * Cyberpunk 暗黑科幻风格登录界面
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Button, Input, message, Tag } from 'antd'
import {
  UserOutlined,
  LockOutlined,
  ThunderboltOutlined,
  EyeInvisibleOutlined,
  EyeOutlined,
} from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { authAPI } from '@/services/api'

// 背景粒子效果
function LoginParticles() {
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      {Array.from({ length: 20 }).map((_, i) => (
        <motion.div
          key={i}
          className="absolute w-0.5 h-0.5 rounded-full bg-neon-blue"
          style={{
            left: `${Math.random() * 100}%`,
            top: `${Math.random() * 100}%`,
          }}
          animate={{
            opacity: [0, 0.8, 0],
            y: [-20, -60],
          }}
          transition={{
            duration: 2 + Math.random() * 3,
            repeat: Infinity,
            delay: Math.random() * 4,
          }}
        />
      ))}
    </div>
  )
}

export function LoginPage() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const handleLogin = async () => {
    if (!username.trim() || !password.trim()) {
      message.warning(t('login.usernameRequired'))
      return
    }

    setLoading(true)
    try {
      const res = await authAPI.login(username, password)
      const { access_token, user, role } = res.data

      localStorage.setItem('scn_access_token', access_token)
      localStorage.setItem('scn_user', JSON.stringify(user))
      localStorage.setItem('scn_user_role', role || user.role || 'viewer')

      message.success(`欢迎回来, ${user.full_name || user.username}`)
      navigate('/dashboard', { replace: true })
    } catch (err: any) {
      message.error(err.message || t('login.failed'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative min-h-screen bg-cyber-black flex items-center justify-center overflow-hidden">
      {/* 背景 */}
      <div className="absolute inset-0 cyber-grid opacity-50" />
      <LoginParticles />

      {/* 旋转装饰环 */}
      <motion.div
        className="absolute w-[500px] h-[500px] border border-neon-blue/10 rounded-full"
        animate={{ rotate: 360 }}
        transition={{ duration: 40, repeat: Infinity, ease: 'linear' }}
      />
      <motion.div
        className="absolute w-[400px] h-[400px] border border-neon-purple/10 rounded-full"
        animate={{ rotate: -360 }}
        transition={{ duration: 35, repeat: Infinity, ease: 'linear' }}
      />

      {/* 登录卡片 */}
      <motion.div
        className="relative z-10 w-full max-w-md mx-4"
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.6 }}
      >
        {/* Logo */}
        <div className="text-center mb-8">
          <motion.div
            className="w-16 h-16 mx-auto mb-4 rounded-full bg-gradient-to-br from-neon-blue to-neon-cyan flex items-center justify-center"
            animate={{
              boxShadow: [
                '0 0 20px rgba(0,212,255,0.4)',
                '0 0 40px rgba(0,212,255,0.7)',
                '0 0 20px rgba(0,212,255,0.4)',
              ],
            }}
            transition={{ duration: 2, repeat: Infinity }}
          >
            <ThunderboltOutlined style={{ fontSize: 28, color: '#fff' }} />
          </motion.div>
          <h1 className="font-display text-2xl font-bold neon-text tracking-wider">
            SCN ENDPOINTS
          </h1>
          <p className="text-text-muted text-xs font-mono uppercase tracking-widest mt-2">
            {t('app.title')}
          </p>
        </div>

        {/* 表单 */}
        <div className="bg-cyber-dark/80 backdrop-blur-md border border-cyber-border rounded-lg p-8">
          <Tag color="cyan" className="mb-6 font-mono text-[10px] tracking-wider">
            {t('login.title')}
          </Tag>

          <div className="space-y-5">
            <div>
              <label className="text-text-secondary text-xs font-mono uppercase tracking-wider mb-1.5 block">
                {t('login.username')}
              </label>
              <Input
                size="large"
                prefix={<UserOutlined className="text-text-muted" />}
                placeholder={t('login.usernameRequired')}
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                onPressEnter={handleLogin}
                className="bg-cyber-black border-cyber-border text-text-primary font-mono"
              />
            </div>

            <div>
              <label className="text-text-secondary text-xs font-mono uppercase tracking-wider mb-1.5 block">
                {t('login.password')}
              </label>
              <Input
                size="large"
                type={showPassword ? 'text' : 'password'}
                prefix={<LockOutlined className="text-text-muted" />}
                suffix={
                  <Button
                    type="text"
                    size="small"
                    icon={showPassword ? <EyeOutlined /> : <EyeInvisibleOutlined />}
                    onClick={() => setShowPassword(!showPassword)}
                    className="text-text-muted"
                  />
                }
                placeholder={t('login.passwordRequired')}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onPressEnter={handleLogin}
                className="bg-cyber-black border-cyber-border text-text-primary font-mono"
              />
            </div>

            <Button
              type="primary"
              size="large"
              block
              loading={loading}
              onClick={handleLogin}
              className="h-12 font-mono text-sm tracking-wider mt-4"
            >
              {t('login.submit')}
            </Button>
          </div>

          <div className="mt-6 pt-4 border-t border-cyber-border text-center"></div>
        </div>
      </motion.div>
    </div>
  )
}
