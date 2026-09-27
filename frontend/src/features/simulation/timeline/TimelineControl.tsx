import { CaretRightOutlined, PauseOutlined } from '@ant-design/icons'
import {
  useCallback,
  useRef,
  type CSSProperties,
  type ChangeEvent,
  type KeyboardEvent,
  type PointerEvent,
} from 'react'
import {
  useTimelinePlayer,
  type TimelinePlayerRuntime,
  type TimelineSpeed,
} from '../hooks/useTimelinePlayer'
import './timeline.css'

/** Localized strings supplied by the integration layer's `timeline` i18n namespace. */
export interface TimelineControlLabels {
  title: string
  play: string
  pause: string
  playbackSpeed: string
  framesPerSecond: (speed: TimelineSpeed) => string
  seek: string
  currentTime: string
  framePosition: (frameIndex: number, frameCount: number) => string
  frameInterval: (minutes: number) => string
  pageHidden: string
  playbackUnavailable: string
  dataInvalid?: string
}

/** Metadata and integration callbacks for the simulation timeline control. */
export interface TimelineControlProps {
  frameCount: number
  frameTimes: readonly string[]
  labels: TimelineControlLabels
  initialFrameIndex?: number
  initialSpeed?: TimelineSpeed
  frameIntervalMinutes?: number
  playbackDisabled?: boolean
  prefetchEnabled?: boolean
  prefetchFrame?: (frameIndex: number, signal: AbortSignal) => Promise<unknown> | unknown
  prefetchKey?: string
  timelineKey?: string
  formatTime?: (isoTime: string) => string
  onFrameChange?: (frameIndex: number) => void
  onPlayingChange?: (isPlaying: boolean) => void
  onPrefetchError?: (error: unknown, frameIndex: number) => void
  runtime?: TimelinePlayerRuntime
  className?: string
}

const SPEED_OPTIONS: readonly TimelineSpeed[] = [1, 2, 4]
const UTC_RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/

function validFrameManifest(frameCount: number, frameTimes: readonly string[]): boolean {
  if (!Number.isSafeInteger(frameCount) || frameCount < 0) return false
  if (frameTimes.length !== frameCount) return false
  let previous = Number.NEGATIVE_INFINITY
  return frameTimes.every((time) => {
    if (typeof time !== 'string' || !UTC_RFC3339_RE.test(time)) return false
    const parsed = Date.parse(time)
    if (!Number.isFinite(parsed) || parsed <= previous) return false
    previous = parsed
    return true
  })
}

/** Accessible play/pause, seek, speed, and simulation-time control surface. */
export function TimelineControl({
  frameCount,
  frameTimes,
  labels,
  initialFrameIndex = 0,
  initialSpeed = 1,
  frameIntervalMinutes = 5,
  playbackDisabled = false,
  prefetchEnabled = true,
  prefetchFrame,
  prefetchKey,
  timelineKey,
  formatTime = (isoTime) => isoTime,
  onFrameChange,
  onPlayingChange,
  onPrefetchError,
  runtime,
  className = '',
}: TimelineControlProps) {
  const manifestValid = validFrameManifest(frameCount, frameTimes)
  const safeFrameCount = manifestValid ? frameCount : 0
  const player = useTimelinePlayer({
    frameCount: safeFrameCount,
    initialFrameIndex,
    initialSpeed,
    playbackDisabled,
    prefetchEnabled,
    prefetchFrame,
    prefetchKey,
    timelineKey,
    onFrameChange,
    onPlayingChange,
    onPrefetchError,
    runtime,
  })
  const rangeRef = useRef<HTMLInputElement>(null)
  const pointerActiveRef = useRef(false)
  const maxFrameIndex = Math.max(0, safeFrameCount - 1)
  const progress = maxFrameIndex === 0 ? 0 : (player.frameIndex / maxFrameIndex) * 100
  const currentIsoTime = manifestValid ? frameTimes[player.frameIndex] : undefined
  const currentTime = currentIsoTime
    ? formatTime(currentIsoTime)
    : labels.dataInvalid ?? 'Timeline data invalid'
  const framePosition = labels.framePosition(player.frameIndex, safeFrameCount)
  const style = { '--timeline-progress': `${progress}%` } as CSSProperties

  const finishScrub = useCallback(
    (cancelled: boolean, finalFrameIndex?: number) => {
      if (!pointerActiveRef.current) return
      pointerActiveRef.current = false
      if (cancelled) {
        player.cancelScrub()
      } else {
        player.endScrub(finalFrameIndex)
      }
    },
    [player]
  )

  const handleRangeChange = (event: ChangeEvent<HTMLInputElement>) => {
    const nextFrameIndex = Number(event.target.value)
    if (pointerActiveRef.current || player.isScrubbing) {
      player.previewSeek(nextFrameIndex)
      return
    }
    player.seek(nextFrameIndex)
  }

  const handlePointerDown = (event: PointerEvent<HTMLInputElement>) => {
    if (event.button > 0 || safeFrameCount <= 1 || playbackDisabled || !manifestValid) return
    pointerActiveRef.current = true
    player.beginScrub()
    if (event.currentTarget.setPointerCapture) {
      event.currentTarget.setPointerCapture(event.pointerId)
    }
  }

  const handlePointerUp = (event: PointerEvent<HTMLInputElement>) => {
    finishScrub(false, Number(event.currentTarget.value))
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  const handlePointerCancel = () => {
    finishScrub(true)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const target = event.target
    const isRange = target === rangeRef.current
    const isInteractiveChild =
      target instanceof HTMLElement && Boolean(target.closest('button, input, select, textarea, a'))

    if (!isRange && isInteractiveChild) return

    switch (event.key) {
      case ' ':
      case 'Spacebar':
        event.preventDefault()
        player.togglePlayback()
        break
      case 'ArrowLeft':
        event.preventDefault()
        player.step(event.shiftKey ? -10 : -1)
        break
      case 'ArrowRight':
        event.preventDefault()
        player.step(event.shiftKey ? 10 : 1)
        break
      case 'Home':
        event.preventDefault()
        player.seek(0)
        break
      case 'End':
        event.preventDefault()
        player.seek(maxFrameIndex)
        break
      default:
        break
    }
  }

  const controlsDisabled =
    safeFrameCount <= 1 || playbackDisabled || player.isPageHidden || !manifestValid

  return (
    <section
      className={`timeline-control ${player.isScrubbing ? 'is-scrubbing' : ''} ${className}`.trim()}
      style={style}
      tabIndex={0}
      aria-label={labels.title}
      onKeyDown={handleKeyDown}
      data-frame-index={player.frameIndex}
      data-playing={player.isPlaying ? 'true' : 'false'}
      data-testid="simulation-frame-timeline"
    >
      <div className="timeline-control__heading">
        <div>
          <h2 className="timeline-control__title">{labels.title}</h2>
        </div>
        <div className="timeline-control__metadata" aria-live="polite">
          <span className="timeline-control__time-label">{labels.currentTime}</span>
          <time className="timeline-control__time" dateTime={currentIsoTime}>
            {currentTime}
          </time>
          <span>{framePosition}</span>
          <span>{labels.frameInterval(frameIntervalMinutes)}</span>
        </div>
      </div>

      {player.isPageHidden ? (
        <div className="timeline-control__notice" role="status">
          {labels.pageHidden}
        </div>
      ) : null}
      {playbackDisabled ? (
        <div className="timeline-control__notice" role="status">
          {labels.playbackUnavailable}
        </div>
      ) : null}
      {!manifestValid ? (
        <div className="timeline-control__notice" role="alert">
          {labels.dataInvalid ?? 'Timeline data invalid'}
        </div>
      ) : null}

      <div className="timeline-control__body">
        <button
          type="button"
          className="timeline-control__play"
          onClick={player.togglePlayback}
          disabled={controlsDisabled}
          aria-label={player.isPlaying ? labels.pause : labels.play}
          aria-pressed={player.isPlaying}
        >
          {player.isPlaying ? <PauseOutlined /> : <CaretRightOutlined />}
        </button>

        <div className="timeline-control__track-wrap">
          <input
            ref={rangeRef}
            className="timeline-control__range"
            type="range"
            min={0}
            max={maxFrameIndex}
            step={1}
            value={player.frameIndex}
            disabled={controlsDisabled}
            aria-label={labels.seek}
            aria-valuetext={`${currentTime}, ${framePosition}`}
            onChange={handleRangeChange}
            onPointerDown={handlePointerDown}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerCancel}
            onLostPointerCapture={() => finishScrub(true)}
          />
          <div className="timeline-control__ticks" aria-hidden="true">
            <span />
            <span />
            <span />
            <span />
            <span />
          </div>
        </div>

        <div className="timeline-control__speeds" role="group" aria-label={labels.playbackSpeed}>
          {SPEED_OPTIONS.map((speed) => (
            <button
              type="button"
              key={speed}
              data-testid={speed === 4 ? 'simulation-playback-4x' : undefined}
              className="timeline-control__speed"
              onClick={() => player.setSpeed(speed)}
              disabled={playbackDisabled}
              aria-label={labels.framesPerSecond(speed)}
              aria-pressed={player.speed === speed}
            >
              {speed}×
            </button>
          ))}
        </div>
      </div>
    </section>
  )
}
