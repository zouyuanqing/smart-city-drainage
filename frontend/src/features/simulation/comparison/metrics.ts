const NOT_AVAILABLE = '—'

function finiteOrNull(value: number | null): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function formatMetricValue(
  value: number | null,
  locale = 'en-US',
  maximumFractionDigits = 6,
  notAvailable = NOT_AVAILABLE
): string {
  const finiteValue = finiteOrNull(value)
  if (finiteValue === null) return notAvailable

  return new Intl.NumberFormat(locale, { maximumFractionDigits }).format(finiteValue)
}

export function formatSignedMetricValue(
  value: number | null,
  locale = 'en-US',
  maximumFractionDigits = 6,
  notAvailable = NOT_AVAILABLE
): string {
  const finiteValue = finiteOrNull(value)
  if (finiteValue === null) return notAvailable
  if (finiteValue > 0) {
    return `+${formatMetricValue(finiteValue, locale, maximumFractionDigits, notAvailable)}`
  }

  return formatMetricValue(finiteValue, locale, maximumFractionDigits, notAvailable)
}

export function formatRelativeDelta(
  value: number | null,
  locale = 'en-US',
  notAvailable = NOT_AVAILABLE
): string {
  const finiteValue = finiteOrNull(value)
  if (finiteValue === null) return notAvailable

  return new Intl.NumberFormat(locale, {
    style: 'percent',
    maximumFractionDigits: 2,
  }).format(finiteValue)
}
