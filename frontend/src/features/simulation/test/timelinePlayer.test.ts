import { renderHook } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { useTimelinePlayer, type TimelinePlayerRuntime } from '../hooks/useTimelinePlayer'

function createManualRuntime() {
  let currentTime = 0
  let nextHandle = 0
  const callbacks = new Map<number, FrameRequestCallback>()

  const runtime: TimelinePlayerRuntime = {
    now: () => currentTime,
    requestFrame: (callback) => {
      nextHandle += 1
      callbacks.set(nextHandle, callback)
      return nextHandle
    },
    cancelFrame: (handle) => {
      callbacks.delete(handle)
    },
  }

  const advance = (milliseconds: number) => {
    currentTime += milliseconds
    const pending = [...callbacks.entries()]
    callbacks.clear()
    pending.forEach(([, callback]) => callback(currentTime))
  }

  return { runtime, advance, pendingCount: () => callbacks.size }
}

describe('useTimelinePlayer', () => {
  it('advances exactly one frame per second at 1x', () => {
    const { runtime, advance, pendingCount } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 4, runtime }))

    act(() => result.current.play())
    act(() => advance(0))
    expect(pendingCount()).toBe(1)
    for (let elapsed = 0; elapsed < 900; elapsed += 100) {
      act(() => advance(100))
    }

    expect(result.current.frameIndex).toBe(0)

    act(() => advance(100))

    expect(result.current.frameIndex).toBe(1)
    expect(result.current.isPlaying).toBe(true)
  })

  it('seeks to a bounded frame and restarts from zero after reaching the end', () => {
    const { runtime } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 4, runtime }))

    act(() => result.current.seek(99))
    expect(result.current.frameIndex).toBe(3)

    act(() => result.current.play())
    expect(result.current.frameIndex).toBe(0)
    expect(result.current.isPlaying).toBe(true)
  })

  it('drops a late RAF gap instead of replaying stale virtual time', () => {
    const { runtime, advance } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 6, runtime }))

    act(() => result.current.play())
    act(() => advance(0))
    act(() => advance(300))
    for (let tick = 0; tick < 9; tick += 1) {
      act(() => advance(100))
    }
    expect(result.current.frameIndex).toBe(0)

    act(() => advance(100))
    expect(result.current.frameIndex).toBe(1)
  })

  it('toggles playback and steps by a bounded frame delta', () => {
    const { runtime } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 5, runtime }))

    act(() => result.current.play())
    act(() => result.current.togglePlayback())
    expect(result.current.isPlaying).toBe(false)
    act(() => result.current.togglePlayback())
    expect(result.current.isPlaying).toBe(true)

    act(() => result.current.step(10))
    expect(result.current.frameIndex).toBe(4)
    act(() => result.current.step(-2))
    expect(result.current.frameIndex).toBe(2)
  })

  it('reports frame and playback state changes through callbacks', () => {
    const onFrameChange = vi.fn()
    const onPlayingChange = vi.fn()
    const { result } = renderHook(() =>
      useTimelinePlayer({
        frameCount: 6,
        initialFrameIndex: 2,
        onFrameChange,
        onPlayingChange,
        runtime: createManualRuntime().runtime,
      })
    )

    expect(onFrameChange).toHaveBeenLastCalledWith(2)
    act(() => result.current.seek(4))
    expect(onFrameChange).toHaveBeenLastCalledWith(4)
    act(() => result.current.play())
    expect(onPlayingChange).toHaveBeenLastCalledWith(true)
    act(() => result.current.pause())
    expect(onPlayingChange).toHaveBeenLastCalledWith(false)
  })

  it('pauses when synchronized playback becomes disabled', () => {
    const { runtime } = createManualRuntime()
    const { result, rerender } = renderHook(
      ({ disabled }) =>
        useTimelinePlayer({
          frameCount: 6,
          playbackDisabled: disabled,
          runtime,
        }),
      { initialProps: { disabled: false } }
    )

    act(() => result.current.play())
    expect(result.current.isPlaying).toBe(true)

    rerender({ disabled: true })
    expect(result.current.isPlaying).toBe(false)
  })

  it('resets index, clock, and playback when the timeline identity changes', () => {
    const { runtime } = createManualRuntime()
    const { result, rerender } = renderHook(
      ({ timelineKey }) =>
        useTimelinePlayer({
          frameCount: 6,
          timelineKey,
          runtime,
        }),
      { initialProps: { timelineKey: 'simulation-a' } }
    )

    act(() => result.current.seek(4))
    act(() => result.current.play())
    rerender({ timelineKey: 'simulation-b' })

    expect(result.current.frameIndex).toBe(0)
    expect(result.current.isPlaying).toBe(false)
  })

  it('stops at the final frame without looping', () => {
    const { runtime, advance } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 3, runtime }))

    act(() => result.current.play())
    act(() => advance(0))
    for (let tick = 0; tick < 20; tick += 1) {
      act(() => advance(100))
    }

    expect(result.current.frameIndex).toBe(2)
    expect(result.current.isPlaying).toBe(false)
  })

  it('prefetches at most two adjacent frames and aborts obsolete requests', async () => {
    const { runtime } = createManualRuntime()
    const requests = new Map<number, { signal: AbortSignal; resolve: () => void }>()
    const prefetchFrame = vi.fn(
      (frameIndex: number, signal: AbortSignal) =>
        new Promise<void>((resolve) => {
          requests.set(frameIndex, { signal, resolve })
        })
    )
    const { result, unmount } = renderHook(() =>
      useTimelinePlayer({
        frameCount: 9,
        initialFrameIndex: 2,
        prefetchFrame,
        runtime,
      })
    )

    expect(prefetchFrame.mock.calls.map(([frameIndex]) => frameIndex)).toEqual([3, 4])

    await act(async () => {
      requests.get(3)?.resolve()
      await Promise.resolve()
    })
    expect(prefetchFrame.mock.calls.map(([frameIndex]) => frameIndex)).toEqual([3, 4, 1])

    act(() => result.current.seek(5))
    expect(requests.get(1)?.signal.aborted).toBe(true)

    await act(async () => {
      requests.get(1)?.resolve()
      await Promise.resolve()
    })
    expect(requests.has(6)).toBe(true)

    unmount()
    expect(requests.get(4)?.signal.aborted).toBe(true)
    expect(requests.get(6)?.signal.aborted).toBe(true)
  })

  it('cancels animation and prefetch work on unmount', () => {
    const { runtime, advance, pendingCount } = createManualRuntime()
    const signals: AbortSignal[] = []
    const prefetchFrame = vi.fn(
      (_frameIndex: number, signal: AbortSignal) =>
        new Promise<void>(() => {
          signals.push(signal)
        })
    )
    const removeEventListener = vi.spyOn(document, 'removeEventListener')
    const { result, unmount } = renderHook(() =>
      useTimelinePlayer({ frameCount: 10, prefetchFrame, runtime })
    )

    act(() => result.current.play())
    act(() => advance(0))
    expect(pendingCount()).toBe(1)
    expect(signals).toHaveLength(2)

    unmount()

    expect(pendingCount()).toBe(0)
    expect(signals.every((signal) => signal.aborted)).toBe(true)
    expect(removeEventListener).toHaveBeenCalledWith('visibilitychange', expect.any(Function))
    removeEventListener.mockRestore()
  })

  it('pauses while the page is hidden and requires explicit resume', () => {
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      value: false,
    })
    const { runtime, advance } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 10, runtime }))

    act(() => result.current.play())
    act(() => {
      Object.defineProperty(document, 'hidden', {
        configurable: true,
        value: true,
      })
      document.dispatchEvent(new Event('visibilitychange'))
    })

    expect(result.current.isPageHidden).toBe(true)
    expect(result.current.isPlaying).toBe(false)
    for (let tick = 0; tick < 10; tick += 1) {
      act(() => advance(100))
    }
    expect(result.current.frameIndex).toBe(0)

    act(() => {
      Object.defineProperty(document, 'hidden', {
        configurable: true,
        value: false,
      })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(result.current.isPlaying).toBe(false)

    act(() => result.current.play())
    expect(result.current.isPlaying).toBe(true)
  })

  it('pauses for a throttled drag preview and resumes the prior state on commit', () => {
    const { runtime, advance } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 10, runtime }))

    act(() => result.current.play())
    act(() => result.current.beginScrub())
    expect(result.current.isPlaying).toBe(false)
    expect(result.current.isScrubbing).toBe(true)

    act(() => result.current.previewSeek(2))
    expect(result.current.frameIndex).toBe(2)
    act(() => result.current.previewSeek(4))
    expect(result.current.frameIndex).toBe(2)

    act(() => advance(100))
    act(() => result.current.previewSeek(4))
    expect(result.current.frameIndex).toBe(4)

    act(() => result.current.endScrub())
    expect(result.current.isScrubbing).toBe(false)
    expect(result.current.isPlaying).toBe(true)
  })

  it('commits the final drag value even when preview throttling rejected it', () => {
    const { runtime } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 10, runtime }))

    act(() => result.current.beginScrub())
    act(() => result.current.previewSeek(2))
    act(() => result.current.previewSeek(4))
    expect(result.current.frameIndex).toBe(2)

    act(() => result.current.endScrub(4))
    expect(result.current.frameIndex).toBe(4)
  })

  it('resets accumulated time when seeking during playback', () => {
    const { runtime, advance } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 10, runtime }))

    act(() => result.current.play())
    act(() => advance(0))
    for (let tick = 0; tick < 6; tick += 1) {
      act(() => advance(100))
    }

    act(() => result.current.seek(4))
    for (let tick = 0; tick < 9; tick += 1) {
      act(() => advance(100))
    }
    expect(result.current.frameIndex).toBe(4)

    act(() => advance(100))
    expect(result.current.frameIndex).toBe(5)
  })

  it('changes speed without carrying stale accumulator time', () => {
    const { runtime, advance } = createManualRuntime()
    const { result } = renderHook(() => useTimelinePlayer({ frameCount: 8, runtime }))

    act(() => result.current.play())
    act(() => advance(0))
    act(() => advance(200))
    expect(result.current.frameIndex).toBe(0)

    act(() => result.current.setSpeed(4))
    expect(result.current.speed).toBe(4)

    for (let tick = 0; tick < 5; tick += 1) {
      act(() => advance(50))
    }
    expect(result.current.frameIndex).toBe(1)
  })

  it('supports 2x and 4x frame rates', () => {
    const cases = [
      { speed: 2 as const, tickDuration: 100, frameDuration: 500 },
      { speed: 4 as const, tickDuration: 100, frameDuration: 250 },
    ]

    cases.forEach(({ speed, tickDuration, frameDuration }) => {
      const { runtime, advance } = createManualRuntime()
      const { result, unmount } = renderHook(() =>
        useTimelinePlayer({ frameCount: 8, initialSpeed: speed, runtime })
      )

      act(() => result.current.play())
      act(() => advance(0))

      let elapsed = 0
      while (elapsed + tickDuration < frameDuration) {
        elapsed += tickDuration
        act(() => advance(tickDuration))
      }
      expect(result.current.frameIndex).toBe(0)

      act(() => advance(frameDuration - elapsed))
      expect(result.current.frameIndex).toBe(1)

      unmount()
    })
  })
})
