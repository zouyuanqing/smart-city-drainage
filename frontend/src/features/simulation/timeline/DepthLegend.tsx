import type { CSSProperties } from 'react'
import './timeline.css'

/** One numeric inundation-depth interval and its non-color-only map swatch. */
export interface DepthLegendStop {
  min: number
  max: number
  color: string
}

/** Localized title, unit, and ordered depth bands rendered by the legend. */
export interface DepthLegendProps {
  title: string
  unit: string
  stops: readonly DepthLegendStop[]
  className?: string
}

const formatDepth = (value: number) => {
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}

/** Renders numeric depth ranges so the map encoding is not communicated by color alone. */
export function DepthLegend({ title, unit, stops, className = '' }: DepthLegendProps) {
  return (
    <aside className={`depth-legend ${className}`.trim()} aria-label={title}>
      <div className="depth-legend__header">
        <span className="depth-legend__title">{title}</span>
        <span className="depth-legend__unit">[{unit}]</span>
      </div>
      <ul className="depth-legend__items" aria-label={title}>
        {stops.map((stop) => {
          const label = Number.isFinite(stop.max)
            ? `${formatDepth(stop.min)}–${formatDepth(stop.max)} ${unit}`
            : `≥${formatDepth(stop.min)} ${unit}`

          return (
            <li className="depth-legend__item" key={`${stop.min}-${stop.max}`}>
              <span
                className="depth-legend__swatch"
                style={{ '--legend-color': stop.color } as CSSProperties}
                aria-hidden="true"
              />
              <span>{label}</span>
            </li>
          )
        })}
      </ul>
    </aside>
  )
}
