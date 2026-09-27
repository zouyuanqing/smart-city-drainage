import { BarChart, LineChart } from 'echarts/charts'
import {
  AriaComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import { use as registerModules } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'

let registered = false

export function ensureEChartsModules(): void {
  if (registered) return

  registerModules([
    LineChart,
    BarChart,
    GridComponent,
    TooltipComponent,
    LegendComponent,
    MarkLineComponent,
    AriaComponent,
    CanvasRenderer,
  ])
  registered = true
}
