import type { AlignedSimulationPoint, SimulationSeriesPoint } from './types'

const UTC_RFC3339_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/

export class TimeAlignmentError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'TimeAlignmentError'
  }
}

function finiteOrNull(value: number | null): number | null {
  if (value === null) return null
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new TimeAlignmentError('series values must be finite numbers or null')
  }
  return Object.is(value, -0) ? 0 : value
}

function parseTimestamp(timestamp: string, side: string): number {
  if (typeof timestamp !== 'string' || !UTC_RFC3339_RE.test(timestamp)) {
    throw new TimeAlignmentError(`${side} timestamp must be UTC RFC3339`)
  }
  const timestampMs = Date.parse(timestamp)
  if (!Number.isFinite(timestampMs)) {
    throw new TimeAlignmentError(`${side} timestamp is invalid`)
  }
  return timestampMs
}

function indexByAbsoluteTime(
  points: readonly SimulationSeriesPoint[],
  side: string
): Map<number, number | null> {
  const indexed = new Map<number, number | null>()
  for (const point of points) {
    const timestampMs = parseTimestamp(point.timestamp, side)
    if (indexed.has(timestampMs)) {
      throw new TimeAlignmentError(`${side} contains a duplicate absolute timestamp`)
    }
    indexed.set(timestampMs, finiteOrNull(point.value))
  }
  return indexed
}

/** Join only equal absolute timestamps; never resample or silently overwrite. */
export function alignSimulationSeries(
  baseline: readonly SimulationSeriesPoint[],
  candidate: readonly SimulationSeriesPoint[]
): AlignedSimulationPoint[] {
  const baselineByTime = indexByAbsoluteTime(baseline, 'baseline')
  const candidateByTime = indexByAbsoluteTime(candidate, 'candidate')
  const timestamps = [...new Set([...baselineByTime.keys(), ...candidateByTime.keys()])].sort(
    (left, right) => left - right
  )

  return timestamps.map((timestampMs) => {
    const baselineValue = finiteOrNull(baselineByTime.get(timestampMs) ?? null)
    const candidateValue = finiteOrNull(candidateByTime.get(timestampMs) ?? null)
    const delta =
      baselineValue !== null && candidateValue !== null
        ? Object.is(candidateValue - baselineValue, -0)
          ? 0
          : candidateValue - baselineValue
        : null

    return {
      timestamp: new Date(timestampMs).toISOString(),
      timestamp_ms: timestampMs,
      baseline: baselineValue,
      candidate: candidateValue,
      delta,
    }
  })
}
