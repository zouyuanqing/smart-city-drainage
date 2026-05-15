/** @type {import('tailwindcss').Config} */

export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class', // 始终使用暗黑模式
  theme: {
    extend: {
      // ========== 暗黑科技风色彩体系 ==========
      colors: {
        // 主色调
        cyber: {
          black:   '#0A0E17',   // 最深背景
          dark:    '#111827',   // 面板背景
          medium:  '#1A2332',   // 卡片背景
          light:   '#243044',   // 悬浮背景
          border:  '#2D3A4A',   // 边框
        },
        // 霓虹色系 (发光效果)
        neon: {
          blue:    '#00D4FF',   // 主数据色
          cyan:    '#00F5FF',   // 强调色
          green:   '#00FF88',   // 安全/正常
          orange:  '#FF8C00',   // 警告
          red:     '#FF3366',   // 危险/告警
          purple:  '#A855F7',   // 装饰
          pink:    '#FF69B4',   // 特殊标注
        },
        // 发光强度 (用于 box-shadow 和 text-shadow)
        glow: {
          blue:    '0 0 10px rgba(0, 212, 255, 0.5), 0 0 30px rgba(0, 212, 255, 0.2)',
          green:   '0 0 10px rgba(0, 255, 136, 0.5), 0 0 30px rgba(0, 255, 136, 0.2)',
          red:     '0 0 10px rgba(255, 51, 102, 0.5), 0 0 30px rgba(255, 51, 102, 0.2)',
          orange:  '0 0 10px rgba(255, 140, 0, 0.5), 0 0 30px rgba(255, 140, 0, 0.2)',
        },
      },

      // ========== 字体 ==========
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
        sans: ['Inter', 'PingFang SC', 'Microsoft YaHei', 'sans-serif'],
        display: ['Orbitron', 'Rajdhani', 'sans-serif'],
      },

      // ========== 动画 ==========
      animation: {
        'pulse-glow': 'pulseGlow 2s ease-in-out infinite',
        'breathing': 'breathing 3s ease-in-out infinite',
        'scan-line': 'scanLine 3s linear infinite',
        'data-flow': 'dataFlow 1.5s ease-in-out infinite',
        'slide-up': 'slideUp 0.5s ease-out',
        'glitch': 'glitch 0.3s ease-in-out',
        'rotate-slow': 'rotateSlow 20s linear infinite',
        'float': 'float 6s ease-in-out infinite',
      },
      keyframes: {
        pulseGlow: {
          '0%, 100%': { opacity: '1', filter: 'brightness(1)' },
          '50%': { opacity: '0.7', filter: 'brightness(1.3)' },
        },
        breathing: {
          '0%, 100%': { transform: 'scale(1)', opacity: '0.8' },
          '50%': { transform: 'scale(1.1)', opacity: '1' },
        },
        scanLine: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100%)' },
        },
        dataFlow: {
          '0%, 100%': { opacity: '0.5', transform: 'translateX(0)' },
          '50%': { opacity: '1', transform: 'translateX(4px)' },
        },
        slideUp: {
          '0%': { transform: 'translateY(20px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        glitch: {
          '0%, 100%': { transform: 'translate(0)' },
          '20%': { transform: 'translate(-2px, 2px)' },
          '40%': { transform: 'translate(2px, -1px)' },
          '60%': { transform: 'translate(-1px, -2px)' },
          '80%': { transform: 'translate(1px, 1px)' },
        },
        rotateSlow: {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
        float: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%': { transform: 'translateY(-10px)' },
        },
      },

      // ========== 背景图案 ==========
      backgroundImage: {
        'grid-pattern': 'linear-gradient(rgba(0,212,255,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(0,212,255,0.05) 1px, transparent 1px)',
        'radial-glow': 'radial-gradient(ellipse at center, rgba(0,212,255,0.1) 0%, transparent 70%)',
      },
      backgroundSize: {
        'grid': '40px 40px',
      },
    },
  },
  plugins: [],
};
