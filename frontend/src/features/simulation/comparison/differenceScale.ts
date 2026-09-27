export const DIFFERENCE_SCALE = {
  negative: '#1D4ED8',
  neutral: '#E2E8F0',
  positive: '#C2410C',
} as const

function parseHexColor(color: string): [number, number, number] {
  return [
    Number.parseInt(color.slice(1, 3), 16),
    Number.parseInt(color.slice(3, 5), 16),
    Number.parseInt(color.slice(5, 7), 16),
  ]
}

function toHex(value: number): string {
  return Math.round(value).toString(16).padStart(2, '0').toUpperCase()
}

function mix(from: string, to: string, amount: number): string {
  const [fromRed, fromGreen, fromBlue] = parseHexColor(from)
  const [toRed, toGreen, toBlue] = parseHexColor(to)
  const mixChannel = (start: number, end: number) => start + (end - start) * amount

  return `#${toHex(mixChannel(fromRed, toRed))}${toHex(mixChannel(fromGreen, toGreen))}${toHex(
    mixChannel(fromBlue, toBlue)
  )}`
}

export function maxAbsoluteDelta(points: readonly { readonly delta: number | null }[]): number {
  return points.reduce((maximum, point) => {
    if (typeof point.delta !== 'number' || !Number.isFinite(point.delta)) return maximum
    return Math.max(maximum, Math.abs(point.delta))
  }, 0)
}

/** Returns a colour from the fixed blue–slate–orange diverging scale. */
export function differenceColorFor(delta: number | null, domain: number): string {
  const normalizedDelta = delta === 0 || Object.is(delta, -0) ? 0 : delta
  if (
    typeof normalizedDelta !== 'number' ||
    !Number.isFinite(normalizedDelta) ||
    normalizedDelta === 0
  ) {
    return DIFFERENCE_SCALE.neutral
  }

  const safeDomain = Number.isFinite(domain) && domain > 0 ? domain : Math.abs(normalizedDelta)
  const intensity = Math.min(1, Math.abs(normalizedDelta) / safeDomain)
  return normalizedDelta < 0
    ? mix(DIFFERENCE_SCALE.neutral, DIFFERENCE_SCALE.negative, intensity)
    : mix(DIFFERENCE_SCALE.neutral, DIFFERENCE_SCALE.positive, intensity)
}

export function differenceScaleCss(domain: number, steps = 11): string {
  if (!Number.isFinite(domain) || domain <= 0) {
    return DIFFERENCE_SCALE.neutral
  }
  const count = Math.max(2, Math.floor(steps))
  const stops = Array.from({ length: count }, (_, index) => {
    const ratio = index / (count - 1)
    const delta = -domain + ratio * domain * 2
    return `${differenceColorFor(delta, domain)} ${Math.round(ratio * 100)}%`
  })

  return `linear-gradient(90deg, ${stops.join(', ')})`
}
