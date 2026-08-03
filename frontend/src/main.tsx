/**
 * 智慧城市神经末梢 — 应用入口
 * Copyright 2024 Smart City Neural Endpoints
 * Licensed under the Apache License, Version 2.0
 */

import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { ConfigProvider, theme } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import App from './App'
import './i18n'
import './styles/globals.css'

// Ant Design 暗黑主题配置
const darkTheme = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: '#00D4FF',
    colorBgBase: '#0A0E17',
    colorBgContainer: '#111827',
    colorBgElevated: '#1A2332',
    colorBorder: '#2D3A4A',
    colorText: '#E8EDF2',
    colorTextSecondary: '#8899AA',
    borderRadius: 4,
    fontFamily: "'Inter', 'PingFang SC', 'Microsoft YaHei', sans-serif",
  },
  components: {
    Layout: {
      bodyBg: '#0A0E17',
      headerBg: '#111827',
      siderBg: '#111827',
    },
    Card: {
      colorBgContainer: '#1A2332',
    },
    Table: {
      colorBgContainer: '#1A2332',
      headerBg: '#243044',
    },
  },
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ConfigProvider theme={darkTheme} locale={zhCN}>
        <App />
      </ConfigProvider>
    </BrowserRouter>
  </React.StrictMode>
)
