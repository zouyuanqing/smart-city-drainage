/**
 * 多源视频播放器组件
 * 支持 HLS (HLS.js) 和本地摄像头 (getUserMedia)。
 * 包含完善的容错处理和友好的UI引导。
 */

import { useEffect, useRef, useState, useCallback } from 'react'
import { Button, Spin, Tag } from 'antd'
import {
  PlayCircleOutlined,
  CameraOutlined,
  ReloadOutlined,
  WarningOutlined,
  LoadingOutlined,
} from '@ant-design/icons'
import Hls from 'hls.js'

interface VideoPlayerProps {
  /** 视频流类型 */
  streamType: 'hls' | 'local'
  /** HLS 流地址 */
  streamUrl?: string
  /** 自动播放 */
  autoPlay?: boolean
  /** 是否静音 */
  muted?: boolean
}

export function VideoPlayer({
  streamType,
  streamUrl,
  autoPlay = true,
  muted = true,
}: VideoPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const hlsRef = useRef<Hls | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const [status, setStatus] = useState<
    'loading' | 'playing' | 'error' | 'permission_denied' | 'idle'
  >('idle')
  const [errorMsg, setErrorMsg] = useState('')

  // ---- HLS 流播放 ----
  useEffect(() => {
    if (streamType !== 'hls' || !streamUrl || !videoRef.current) return

    const video = videoRef.current

    const startHls = () => {
      setStatus('loading')

      if (Hls.isSupported()) {
        const hls = new Hls({
          enableWorker: true,
          lowLatencyMode: true,
          backBufferLength: 90,
          maxBufferLength: 30,
          manifestLoadingTimeOut: 15000,
          manifestLoadingMaxRetry: 3,
        })

        hls.loadSource(streamUrl)
        hls.attachMedia(video)

        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          if (autoPlay) {
            video.play().catch(() => {})
          }
          setStatus('playing')
        })

        hls.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal) {
            setStatus('error')
            setErrorMsg(`HLS 播放错误: ${data.type} - ${data.details}`)
            hls.destroy()
          }
        })

        hlsRef.current = hls
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        // Safari 原生 HLS 支持
        video.src = streamUrl
        video.addEventListener('loadedmetadata', () => {
          if (autoPlay) video.play().catch(() => {})
          setStatus('playing')
        })
      } else {
        setStatus('error')
        setErrorMsg('浏览器不支持 HLS 播放')
      }
    }

    startHls()

    return () => {
      if (hlsRef.current) {
        hlsRef.current.destroy()
        hlsRef.current = null
      }
      video.src = ''
      video.load()
    }
  }, [streamType, streamUrl, autoPlay])

  // ---- 本地摄像头 ----
  const startLocalCamera = useCallback(async () => {
    if (!videoRef.current) return

    setStatus('loading')
    setErrorMsg('')

    try {
      // 检查 HTTPS
      if (window.location.protocol === 'http:' && window.location.hostname !== 'localhost') {
        throw new Error('浏览器摄像头需要 HTTPS 安全连接或 localhost 环境')
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 1920 },
          height: { ideal: 1080 },
          facingMode: 'environment',
        },
        audio: false,
      })

      videoRef.current.srcObject = stream
      await videoRef.current.play()
      setStatus('playing')
    } catch (err: any) {
      console.error('摄像头启动失败:', err)

      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        setStatus('permission_denied')
        setErrorMsg('摄像头权限被拒绝，请在浏览器设置中允许摄像头访问')
      } else if (err.name === 'NotFoundError') {
        setStatus('error')
        setErrorMsg('未检测到摄像头设备')
      } else if (err.name === 'NotReadableError') {
        setStatus('error')
        setErrorMsg('摄像头被其他应用占用')
      } else {
        setStatus('error')
        setErrorMsg(err.message || '摄像头启动失败')
      }
    }
  }, [])

  const stopLocalCamera = useCallback(() => {
    if (videoRef.current?.srcObject) {
      const stream = videoRef.current.srcObject as MediaStream
      stream.getTracks().forEach((track) => track.stop())
      videoRef.current.srcObject = null
    }
    setStatus('idle')
  }, [])

  // 组件卸载时清理
  useEffect(() => {
    return () => {
      if (streamType === 'local') stopLocalCamera()
      if (hlsRef.current) hlsRef.current.destroy()
    }
  }, [streamType, stopLocalCamera])

  // ---- 截取当前帧 ----
  const captureFrame = useCallback((): string | null => {
    if (!videoRef.current || !canvasRef.current) return null

    const video = videoRef.current
    const canvas = canvasRef.current
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight

    const ctx = canvas.getContext('2d')
    if (!ctx) return null

    ctx.drawImage(video, 0, 0)
    return canvas.toDataURL('image/jpeg', 0.85)
  }, [])

  // ---- 渲染 ----
  return (
    <div className="relative w-full h-full">
      {/* 视频元素 */}
      <video
        ref={videoRef}
        className="w-full h-full object-cover"
        muted={muted}
        playsInline
        loop={streamType === 'hls'}
      />

      {/* 隐藏 Canvas (用于截图) */}
      <canvas ref={canvasRef} className="hidden" />

      {/* 加载状态 */}
      {status === 'loading' && (
        <div className="absolute inset-0 flex items-center justify-center bg-cyber-black/70 z-20">
          <div className="text-center">
            <Spin indicator={<LoadingOutlined style={{ fontSize: 32, color: '#00D4FF' }} spin />} />
            <p className="text-text-muted text-xs mt-2">正在加载视频流...</p>
          </div>
        </div>
      )}

      {/* 本地摄像头待启动状态 */}
      {streamType === 'local' && status === 'idle' && (
        <div className="absolute inset-0 flex items-center justify-center bg-cyber-black/80 z-20">
          <div className="text-center">
            <CameraOutlined style={{ fontSize: 40, color: '#00D4FF' }} />
            <p className="text-text-secondary text-sm mt-3 mb-4">点击启动本地摄像头</p>
            <Button type="primary" icon={<PlayCircleOutlined />} onClick={startLocalCamera}>
              启动摄像头
            </Button>
          </div>
        </div>
      )}

      {/* 权限拒绝引导 */}
      {status === 'permission_denied' && (
        <div className="absolute inset-0 flex items-center justify-center bg-cyber-black/80 z-20">
          <div className="text-center max-w-xs p-4">
            <WarningOutlined style={{ fontSize: 36, color: '#FF8C00' }} />
            <p className="text-neon-orange text-sm mt-2 mb-1">摄像头权限被拒绝</p>
            <p className="text-text-muted text-xs mb-3">{errorMsg}</p>
            <div className="text-text-muted text-[10px]">
              💡 请在浏览器地址栏左侧的锁图标中允许摄像头权限，然后刷新页面重试
            </div>
            <Button
              size="small"
              onClick={startLocalCamera}
              className="mt-3"
              icon={<ReloadOutlined />}
            >
              重试
            </Button>
          </div>
        </div>
      )}

      {/* 错误状态 */}
      {status === 'error' && streamType === 'hls' && (
        <div className="absolute inset-0 flex items-center justify-center bg-cyber-black/80 z-20">
          <div className="text-center max-w-xs p-4">
            <WarningOutlined style={{ fontSize: 36, color: '#FF3366' }} />
            <p className="text-neon-red text-sm mt-2">视频流加载失败</p>
            <p className="text-text-muted text-xs mt-1 mb-3">{errorMsg}</p>
            <Button size="small" onClick={() => window.location.reload()} icon={<ReloadOutlined />}>
              刷新
            </Button>
          </div>
        </div>
      )}

      {/* 控制栏 */}
      {streamType === 'local' && status === 'playing' && (
        <div className="absolute bottom-2 left-2 right-2 flex items-center justify-between z-20">
          <Tag color="green" className="font-mono text-[10px]">
            LIVE
          </Tag>
          <div className="flex gap-2">
            <Button
              size="small"
              onClick={() => {
                const frame = captureFrame()
                if (frame) console.log('Frame captured:', frame.substring(0, 50) + '...')
              }}
              className="font-mono text-[10px]"
            >
              📸 截帧
            </Button>
            <Button size="small" danger onClick={stopLocalCamera} className="font-mono text-[10px]">
              ⏹ 停止
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
