import { useCallback, useEffect, useRef, useState } from 'react'

/** Canonical playback multipliers: 1, 2, or 4 discrete simulation frames per second. */
export type TimelineSpeed = 1 | 2 | 4

/** Injectable clock and RAF scheduler used to make virtual-time tests deterministic. */
export interface TimelinePlayerRuntime {
  now: () => number
  requestFrame: (callback: FrameRequestCallback) => number
  cancelFrame: (handle: number) => void
}

/** Configuration for the single discrete-frame playback clock and adjacent-frame prefetch. */
export interface UseTimelinePlayerOptions {
  frameCount: number
  initialFrameIndex?: number
  initialSpeed?: TimelineSpeed
  playbackDisabled?: boolean
  prefetchEnabled?: boolean
  prefetchFrame?: (frameIndex: number, signal: AbortSignal) => Promise<unknown> | unknown
  prefetchKey?: string
  onFrameChange?: (frameIndex: number) => void
  onPlayingChange?: (isPlaying: boolean) => void
  onPrefetchError?: (error: unknown, frameIndex: number) => void
  runtime?: TimelinePlayerRuntime
  timelineKey?: string
}

/** Commands and read-only state exposed by the timeline controller. */
export interface TimelinePlayerController {
  frameIndex: number
  isPlaying: boolean
  isPageHidden: boolean
  isScrubbing: boolean
  prefetchedFrameIndices: readonly number[]
  speed: TimelineSpeed
  play: () => void
  pause: () => void
  togglePlayback: () => void
  seek: (frameIndex: number) => void
  step: (delta: number) => void
  beginScrub: () => void
  previewSeek: (frameIndex: number) => void
  endScrub: (finalFrameIndex?: number) => void
  cancelScrub: () => void
  clearPrefetch: () => void
  setSpeed: (speed: TimelineSpeed) => void
}

const defaultRuntime: TimelinePlayerRuntime = {
  now: () => performance.now(),
  requestFrame: (callback) => requestAnimationFrame(callback),
  cancelFrame: (handle) => cancelAnimationFrame(handle),
}

const clampFrameIndex = (frameIndex: number, frameCount: number) => {
  if (!Number.isSafeInteger(frameCount) || frameCount <= 0) return 0
  if (!Number.isFinite(frameIndex)) return 0
  return Math.min(Math.max(Math.trunc(frameIndex), 0), frameCount - 1)
}

function errorStatus(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null) return undefined
  const status = (error as { status?: unknown }).status
  return typeof status === 'number' && Number.isFinite(status) ? status : undefined
}

function errorCode(error: unknown): string | undefined {
  if (typeof error !== 'object' || error === null) return undefined
  const code = (error as { code?: unknown }).code
  return typeof code === 'string' ? code : undefined
}

function retryAfterMs(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null) return undefined
  const seconds = (error as { retryAfterSeconds?: unknown }).retryAfterSeconds
  return typeof seconds === 'number' && Number.isFinite(seconds)
    ? Math.max(0, seconds * 1_000)
    : undefined
}

interface PrefetchFailure {
  attempts: number
  nextAt: number
  terminal: boolean
}

/**
 * Drives discrete simulation frames without hydraulic-state interpolation.
 * The caller owns frame decoding/cache data; this hook owns only clock,
 * navigation, visibility, and cancellable adjacent-frame prefetch state.
 */
export function useTimelinePlayer({
  frameCount,
  initialFrameIndex = 0,
  initialSpeed = 1,
  playbackDisabled = false,
  prefetchEnabled = true,
  prefetchFrame,
  prefetchKey = '',
  onFrameChange,
  onPlayingChange,
  onPrefetchError,
  runtime = defaultRuntime,
  timelineKey = '',
}: UseTimelinePlayerOptions): TimelinePlayerController {
  const [frameIndex, setFrameIndex] = useState(() => clampFrameIndex(initialFrameIndex, frameCount))
  const [isPlaying, setIsPlaying] = useState(false)
  const [isPageHidden, setIsPageHidden] = useState(
    () => typeof document !== 'undefined' && document.hidden
  )
  const [isScrubbing, setIsScrubbing] = useState(false)
  const [speed, setSpeedState] = useState<TimelineSpeed>(initialSpeed)
  const [prefetchedFrameIndices, setPrefetchedFrameIndices] = useState<readonly number[]>([])
  const [playbackEpoch, setPlaybackEpoch] = useState(0)
  const frameIndexRef = useRef(frameIndex)
  const isPlayingRef = useRef(false)
  const isPageHiddenRef = useRef(isPageHidden)
  const isScrubbingRef = useRef(false)
  const resumeAfterScrubRef = useRef(false)
  const scrubOriginIndexRef = useRef(frameIndex)
  const lastPreviewAtRef = useRef(Number.NEGATIVE_INFINITY)
  const mountedRef = useRef(true)
  const runtimeRef = useRef(runtime)
  const prefetchFrameRef = useRef(prefetchFrame)
  const prefetchEnabledRef = useRef(prefetchEnabled)
  const prefetchKeyRef = useRef(prefetchKey)
  const prefetchTimelineKeyRef = useRef(timelineKey)
  const onPrefetchErrorRef = useRef(onPrefetchError)
  const onFrameChangeRef = useRef(onFrameChange)
  const onPlayingChangeRef = useRef(onPlayingChange)
  const timelineKeyRef = useRef(timelineKey)
  const desiredPrefetchIndicesRef = useRef<number[]>([])
  const prefetchInFlightRef = useRef(new Map<number, AbortController>())
  const prefetchedFramesRef = useRef(new Set<number>())
  const prefetchFailuresRef = useRef(new Map<number, PrefetchFailure>())
  const prefetchTimersRef = useRef(new Map<number, ReturnType<typeof setTimeout>>())
  const pumpPrefetchRef = useRef<() => void>(() => undefined)

  runtimeRef.current = runtime
  prefetchFrameRef.current = prefetchFrame
  prefetchEnabledRef.current = prefetchEnabled
  onPrefetchErrorRef.current = onPrefetchError
  onFrameChangeRef.current = onFrameChange
  onPlayingChangeRef.current = onPlayingChange

  const setPlaying = useCallback((playing: boolean) => {
    isPlayingRef.current = playing
    setIsPlaying(playing)
  }, [])

  const updateFrameIndex = useCallback(
    (nextFrameIndex: number) => {
      const clamped = clampFrameIndex(nextFrameIndex, frameCount)
      frameIndexRef.current = clamped
      setFrameIndex(clamped)
    },
    [frameCount]
  )

  const pumpPrefetch = useCallback(() => {
    const prefetch = prefetchFrameRef.current
    if (!mountedRef.current || !prefetchEnabledRef.current || !prefetch) return

    const now = runtimeRef.current.now()
    for (const frameIndex of desiredPrefetchIndicesRef.current) {
      if (prefetchInFlightRef.current.size >= 2) break
      if (
        prefetchInFlightRef.current.has(frameIndex) ||
        prefetchedFramesRef.current.has(frameIndex)
      )
        continue
      const failure = prefetchFailuresRef.current.get(frameIndex)
      if (failure?.terminal) continue
      if (failure && failure.nextAt > now) continue

      const controller = new AbortController()
      prefetchInFlightRef.current.set(frameIndex, controller)
      let request: Promise<unknown>
      try {
        request = Promise.resolve(prefetch(frameIndex, controller.signal))
      } catch (error) {
        prefetchInFlightRef.current.delete(frameIndex)
        if (!controller.signal.aborted) {
          onPrefetchErrorRef.current?.(error, frameIndex)
          const attempts = (failure?.attempts ?? 0) + 1
          const status = errorStatus(error)
          const code = errorCode(error)
          const retryable =
            status === 429 ||
            (status === 503 && code === 'DEPENDENCY_NOT_READY') ||
            status === 0 ||
            status === undefined ||
            code === 'DEPENDENCY_NOT_READY' ||
            code === 'RATE_LIMITED'
          const delay = retryAfterMs(error) ?? [500, 1_000, 2_000][Math.min(attempts - 1, 2)]
          const failureState = {
            attempts,
            nextAt: retryable && attempts <= 2 ? now + delay : Number.POSITIVE_INFINITY,
            terminal: !retryable || attempts > 2,
          }
          prefetchFailuresRef.current.set(frameIndex, failureState)
          if (!failureState.terminal) {
            const timer = setTimeout(
              () => {
                prefetchTimersRef.current.delete(frameIndex)
                pumpPrefetchRef.current()
              },
              Math.max(0, failureState.nextAt - runtimeRef.current.now())
            )
            prefetchTimersRef.current.set(frameIndex, timer)
          }
        }
        continue
      }

      request
        .then(() => {
          if (controller.signal.aborted) return
          prefetchedFramesRef.current.add(frameIndex)
          prefetchFailuresRef.current.delete(frameIndex)
          if (mountedRef.current) {
            setPrefetchedFrameIndices(
              [...prefetchedFramesRef.current].sort((left, right) => left - right)
            )
          }
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return
          onPrefetchErrorRef.current?.(error, frameIndex)
          const status = errorStatus(error)
          const code = errorCode(error)
          const attempts = (prefetchFailuresRef.current.get(frameIndex)?.attempts ?? 0) + 1
          const terminalStatus = status === 409 || status === 410 || status === 422
          const retryable =
            !terminalStatus &&
            (status === 429 ||
              (status === 503 && code === 'DEPENDENCY_NOT_READY') ||
              status === 0 ||
              code === 'DEPENDENCY_NOT_READY' ||
              code === 'RATE_LIMITED')
          const delay = retryAfterMs(error) ?? [500, 1_000, 2_000][Math.min(attempts - 1, 2)]
          prefetchFailuresRef.current.set(frameIndex, {
            attempts,
            nextAt: retryable && attempts <= 2 ? now + delay : Number.POSITIVE_INFINITY,
            terminal: !retryable || attempts > 2,
          })
          if (status === 409 || status === 410) setPlaying(false)
        })
        .finally(() => {
          if (prefetchInFlightRef.current.get(frameIndex) === controller) {
            prefetchInFlightRef.current.delete(frameIndex)
          }
          if (mountedRef.current && !prefetchFailuresRef.current.get(frameIndex)?.terminal) {
            const failureState = prefetchFailuresRef.current.get(frameIndex)
            if (!failureState || failureState.nextAt <= runtimeRef.current.now()) {
              pumpPrefetchRef.current()
            } else if (!prefetchTimersRef.current.has(frameIndex)) {
              const timer = setTimeout(
                () => {
                  prefetchTimersRef.current.delete(frameIndex)
                  pumpPrefetchRef.current()
                },
                Math.max(0, failureState.nextAt - runtimeRef.current.now())
              )
              prefetchTimersRef.current.set(frameIndex, timer)
            }
          }
        })
    }
  }, [setPlaying])

  pumpPrefetchRef.current = pumpPrefetch

  const clearPrefetch = useCallback(() => {
    desiredPrefetchIndicesRef.current = []
    prefetchInFlightRef.current.forEach((controller) => controller.abort())
    prefetchInFlightRef.current.clear()
    prefetchTimersRef.current.forEach((timer) => clearTimeout(timer))
    prefetchTimersRef.current.clear()
    prefetchFailuresRef.current.clear()
    prefetchedFramesRef.current.clear()
    setPrefetchedFrameIndices([])
  }, [])

  useEffect(() => {
    mountedRef.current = true
    const inFlight = prefetchInFlightRef.current
    const timers = prefetchTimersRef.current
    const failures = prefetchFailuresRef.current
    const prefetchedFrames = prefetchedFramesRef.current
    return () => {
      mountedRef.current = false
      desiredPrefetchIndicesRef.current = []
      inFlight.forEach((controller) => controller.abort())
      inFlight.clear()
      timers.forEach((timer) => clearTimeout(timer))
      timers.clear()
      failures.clear()
      prefetchedFrames.clear()
    }
  }, [])

  useEffect(() => {
    onFrameChangeRef.current?.(frameIndex)
  }, [frameIndex])

  useEffect(() => {
    onPlayingChangeRef.current?.(isPlaying)
  }, [isPlaying])

  useEffect(() => {
    if (playbackDisabled || frameCount <= 1 || isPageHidden) {
      resumeAfterScrubRef.current = false
      if (isScrubbingRef.current) {
        isScrubbingRef.current = false
        setIsScrubbing(false)
      }
    }
    if (!playbackDisabled && frameCount > 1 && !isPageHidden) return
    if (!isPlayingRef.current) return
    setPlaying(false)
    setPlaybackEpoch((epoch) => epoch + 1)
  }, [frameCount, isPageHidden, playbackDisabled, setPlaying])

  useEffect(() => {
    if (timelineKeyRef.current === timelineKey) return
    timelineKeyRef.current = timelineKey
    isScrubbingRef.current = false
    resumeAfterScrubRef.current = false
    setIsScrubbing(false)
    setPlaying(false)
    updateFrameIndex(initialFrameIndex)
    clearPrefetch()
    setPlaybackEpoch((epoch) => epoch + 1)
  }, [clearPrefetch, initialFrameIndex, setPlaying, timelineKey, updateFrameIndex])

  useEffect(() => {
    const clampedFrameIndex = clampFrameIndex(frameIndexRef.current, frameCount)
    if (clampedFrameIndex === frameIndexRef.current) return
    setPlaying(false)
    updateFrameIndex(clampedFrameIndex)
    setPlaybackEpoch((epoch) => epoch + 1)
  }, [frameCount, setPlaying, updateFrameIndex])

  useEffect(() => {
    if (prefetchTimelineKeyRef.current !== timelineKey) {
      prefetchTimelineKeyRef.current = timelineKey
      desiredPrefetchIndicesRef.current = []
      prefetchInFlightRef.current.forEach((controller) => controller.abort())
      prefetchInFlightRef.current.clear()
      prefetchTimersRef.current.forEach((timer) => clearTimeout(timer))
      prefetchTimersRef.current.clear()
      prefetchFailuresRef.current.clear()
      prefetchedFramesRef.current.clear()
      setPrefetchedFrameIndices([])
      return
    }

    if (prefetchKeyRef.current !== prefetchKey) {
      prefetchKeyRef.current = prefetchKey
      prefetchInFlightRef.current.forEach((controller) => controller.abort())
      prefetchInFlightRef.current.clear()
      prefetchTimersRef.current.forEach((timer) => clearTimeout(timer))
      prefetchTimersRef.current.clear()
      prefetchFailuresRef.current.clear()
      prefetchedFramesRef.current.clear()
    }

    if (!prefetchEnabled || !prefetchFrame || frameCount <= 1) {
      desiredPrefetchIndicesRef.current = []
      prefetchInFlightRef.current.forEach((controller) => controller.abort())
      prefetchInFlightRef.current.clear()
      prefetchTimersRef.current.forEach((timer) => clearTimeout(timer))
      prefetchTimersRef.current.clear()
      prefetchFailuresRef.current.clear()
      prefetchedFramesRef.current.clear()
      setPrefetchedFrameIndices([])
      return
    }

    const desired = [frameIndex + 1, frameIndex + 2, frameIndex - 1].filter(
      (candidate, index, candidates) =>
        candidate >= 0 && candidate < frameCount && candidates.indexOf(candidate) === index
    )
    desiredPrefetchIndicesRef.current = desired

    prefetchFailuresRef.current.forEach((failure, failedIndex) => {
      if (!desired.includes(failedIndex)) {
        const timer = prefetchTimersRef.current.get(failedIndex)
        if (timer) clearTimeout(timer)
        prefetchTimersRef.current.delete(failedIndex)
        prefetchFailuresRef.current.delete(failedIndex)
      }
    })
    prefetchInFlightRef.current.forEach((controller, inFlightIndex) => {
      if (!desired.includes(inFlightIndex)) {
        controller.abort()
      }
    })
    prefetchedFramesRef.current.forEach((completedIndex) => {
      if (!desired.includes(completedIndex)) {
        prefetchedFramesRef.current.delete(completedIndex)
      }
    })
    setPrefetchedFrameIndices([...prefetchedFramesRef.current].sort((left, right) => left - right))
    pumpPrefetch()
  }, [
    frameCount,
    frameIndex,
    prefetchEnabled,
    prefetchFrame,
    prefetchKey,
    pumpPrefetch,
    timelineKey,
  ])

  useEffect(() => {
    const handleVisibilityChange = () => {
      const hidden = document.hidden
      isPageHiddenRef.current = hidden
      setIsPageHidden(hidden)
      if (hidden) {
        setPlaying(false)
        setPlaybackEpoch((epoch) => epoch + 1)
      }
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [setPlaying])

  const pause = useCallback(() => {
    setPlaying(false)
  }, [setPlaying])

  const seek = useCallback(
    (nextFrameIndex: number) => {
      updateFrameIndex(nextFrameIndex)
      setPlaybackEpoch((epoch) => epoch + 1)
    },
    [updateFrameIndex]
  )

  const step = useCallback(
    (delta: number) => {
      const nextFrameIndex = clampFrameIndex(frameIndexRef.current + Math.trunc(delta), frameCount)
      updateFrameIndex(nextFrameIndex)
      setPlaybackEpoch((epoch) => epoch + 1)
      if (nextFrameIndex >= frameCount - 1 && isPlayingRef.current) {
        setPlaying(false)
      }
    },
    [frameCount, setPlaying, updateFrameIndex]
  )

  const beginScrub = useCallback(() => {
    if (isScrubbingRef.current) return
    isScrubbingRef.current = true
    resumeAfterScrubRef.current = isPlayingRef.current
    scrubOriginIndexRef.current = frameIndexRef.current
    lastPreviewAtRef.current = Number.NEGATIVE_INFINITY
    setIsScrubbing(true)
    setPlaying(false)
    setPlaybackEpoch((epoch) => epoch + 1)
  }, [setPlaying])

  const previewSeek = useCallback(
    (nextFrameIndex: number) => {
      if (!isScrubbingRef.current) return
      const now = runtimeRef.current.now()
      if (now - lastPreviewAtRef.current < 100) return
      lastPreviewAtRef.current = now
      updateFrameIndex(nextFrameIndex)
      setPlaybackEpoch((epoch) => epoch + 1)
    },
    [updateFrameIndex]
  )

  const endScrub = useCallback(
    (finalFrameIndex?: number) => {
      if (!isScrubbingRef.current) return
      if (typeof finalFrameIndex === 'number') {
        updateFrameIndex(finalFrameIndex)
      }
      isScrubbingRef.current = false
      setIsScrubbing(false)
      const shouldResume =
        resumeAfterScrubRef.current &&
        !playbackDisabled &&
        !isPageHiddenRef.current &&
        frameIndexRef.current < frameCount - 1
      resumeAfterScrubRef.current = false
      setPlaying(shouldResume)
      setPlaybackEpoch((epoch) => epoch + 1)
    },
    [frameCount, playbackDisabled, setPlaying, updateFrameIndex]
  )

  const cancelScrub = useCallback(() => {
    if (!isScrubbingRef.current) return
    updateFrameIndex(scrubOriginIndexRef.current)
    isScrubbingRef.current = false
    setIsScrubbing(false)
    const shouldResume =
      resumeAfterScrubRef.current && !playbackDisabled && !isPageHiddenRef.current
    resumeAfterScrubRef.current = false
    setPlaying(shouldResume)
    setPlaybackEpoch((epoch) => epoch + 1)
  }, [playbackDisabled, setPlaying, updateFrameIndex])

  const setSpeed = useCallback((nextSpeed: TimelineSpeed) => {
    if (nextSpeed === 1 || nextSpeed === 2 || nextSpeed === 4) {
      setSpeedState(nextSpeed)
    }
  }, [])

  const play = useCallback(() => {
    if (frameCount <= 1 || playbackDisabled || isPageHiddenRef.current) return
    if (frameIndexRef.current >= frameCount - 1) {
      updateFrameIndex(0)
    }
    setPlaying(true)
  }, [frameCount, playbackDisabled, setPlaying, updateFrameIndex])

  const togglePlayback = useCallback(() => {
    if (isPlayingRef.current) {
      pause()
      return
    }
    play()
  }, [pause, play])

  useEffect(() => {
    if (!isPlaying || frameCount <= 1 || playbackDisabled) return

    let handle = 0
    let previousTime = runtimeRef.current.now()
    let accumulator = 0
    const frameDuration = 1000 / speed

    const tick = () => {
      const currentTime = runtimeRef.current.now()
      const elapsed = Math.max(0, currentTime - previousTime)
      previousTime = currentTime
      accumulator = elapsed > 250 ? 0 : accumulator + elapsed

      const frameSteps = Math.floor(accumulator / frameDuration)
      if (frameSteps > 0) {
        accumulator -= frameSteps * frameDuration
        const nextFrameIndex = Math.min(frameIndexRef.current + frameSteps, frameCount - 1)
        updateFrameIndex(nextFrameIndex)
        if (nextFrameIndex >= frameCount - 1) {
          setPlaying(false)
          return
        }
      }

      handle = runtime.requestFrame(tick)
    }

    handle = runtime.requestFrame(tick)

    return () => {
      runtime.cancelFrame(handle)
    }
  }, [
    frameCount,
    isPlaying,
    playbackDisabled,
    playbackEpoch,
    runtime,
    setPlaying,
    speed,
    updateFrameIndex,
  ])

  return {
    frameIndex,
    isPlaying,
    isPageHidden,
    isScrubbing,
    prefetchedFrameIndices,
    speed,
    play,
    pause,
    togglePlayback,
    seek,
    step,
    beginScrub,
    previewSeek,
    endScrub,
    cancelScrub,
    clearPrefetch,
    setSpeed,
  }
}
