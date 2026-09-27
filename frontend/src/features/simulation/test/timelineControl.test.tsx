import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DepthLegend } from '../timeline/DepthLegend'
import { TimelineControl } from '../timeline/TimelineControl'
import type { TimelinePlayerRuntime } from '../hooks/useTimelinePlayer'

const labels = {
  title: '内涝回放时间轴',
  play: '播放',
  pause: '暂停',
  playbackSpeed: '回放倍速',
  framesPerSecond: (speed: number) => `${speed} 帧每秒`,
  seek: '拖拽时间轴',
  currentTime: '模拟时刻',
  framePosition: (index: number, total: number) => `帧 ${index + 1} / ${total}`,
  frameInterval: (minutes: number) => `${minutes} 分钟/帧`,
  pageHidden: '页面已隐藏，回放已暂停',
  playbackUnavailable: '同步回放不可用',
}

function createManualRuntime(): TimelinePlayerRuntime {
  const currentTime = 0
  return {
    now: () => currentTime,
    requestFrame: () => 1,
    cancelFrame: () => undefined,
  }
}

describe('TimelineControl', () => {
  it('renders time metadata and supports playback, speed, and keyboard seeking', () => {
    render(
      <TimelineControl
        frameCount={3}
        frameTimes={['2026-09-24T00:00:00Z', '2026-09-24T00:05:00Z', '2026-09-24T00:10:00Z']}
        initialFrameIndex={1}
        initialSpeed={1}
        labels={labels}
        runtime={createManualRuntime()}
      />
    )

    expect(screen.getByText('2026-09-24T00:05:00Z')).toBeInTheDocument()
    expect(screen.getByText('帧 2 / 3')).toBeInTheDocument()
    expect(screen.getByText('5 分钟/帧')).toBeInTheDocument()

    const slider = screen.getByRole('slider', { name: '拖拽时间轴' })
    fireEvent.keyDown(slider, { key: 'ArrowRight', shiftKey: true })
    expect(screen.getByText('帧 3 / 3')).toBeInTheDocument()

    fireEvent.keyDown(slider, { key: 'Home' })
    expect(screen.getByText('帧 1 / 3')).toBeInTheDocument()

    const speed4 = screen.getByRole('button', { name: '4 帧每秒' })
    fireEvent.click(speed4)
    expect(speed4).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByRole('button', { name: '播放' }))
    expect(screen.getByRole('button', { name: '暂停' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('pauses during pointer scrubbing and restores the prior playing state', () => {
    render(
      <TimelineControl
        frameCount={4}
        frameTimes={[
          '2026-09-24T00:00:00Z',
          '2026-09-24T00:05:00Z',
          '2026-09-24T00:10:00Z',
          '2026-09-24T00:15:00Z',
        ]}
        labels={labels}
        runtime={createManualRuntime()}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: '播放' }))
    const slider = screen.getByRole('slider', { name: '拖拽时间轴' })
    fireEvent.pointerDown(slider, { pointerId: 1 })
    fireEvent.change(slider, { target: { value: '2' } })

    expect(screen.getByRole('button', { name: '播放' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByText('帧 3 / 4')).toBeInTheDocument()

    fireEvent.pointerUp(slider, { pointerId: 1 })
    expect(screen.getByRole('button', { name: '暂停' })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('DepthLegend', () => {
  it('shows numeric ranges and units instead of color-only swatches', () => {
    render(
      <DepthLegend
        title="积水深度"
        unit="m"
        stops={[
          { min: 0, max: 0.15, color: '#2f6bff' },
          { min: 0.15, max: 0.3, color: '#00c2ff' },
          { min: 0.3, max: 0.5, color: '#ff8c00' },
          { min: 0.5, max: Number.POSITIVE_INFINITY, color: '#ff3366' },
        ]}
      />
    )

    expect(screen.getByRole('list', { name: '积水深度' })).toBeInTheDocument()
    expect(screen.getByText('0–0.15 m')).toBeInTheDocument()
    expect(screen.getByText('≥0.5 m')).toBeInTheDocument()
  })
})
