import { simulationApi } from '../api'
import type { SimulationApiClient } from '../api/client'
import type { SimulationFrame } from '../model/types'
import { alignSimulationSeries, type SimulationSeriesPoint } from '../comparison'

const MAX_COMPARISON_SERIES_POINTS = 240

function finiteMax(values: readonly (number | null | undefined)[] | undefined): number | null {
  if (!values) return null
  const finite = values.filter(
    (value): value is number => typeof value === 'number' && Number.isFinite(value)
  )
  return finite.length > 0 ? Math.max(...finite) : null
}

function frameMetric(frame: SimulationFrame, metricKey: string | undefined): number | null {
  const key = (metricKey ?? 'depth_m').toLowerCase()
  const nodeDepth = finiteMax(frame.nodes?.depth_m)
  const pipeDepth = finiteMax(frame.pipes?.depth_m)
  const pondingDepth = finiteMax(frame.ponding?.depth_m)
  const depth = finiteMax([nodeDepth, pipeDepth, pondingDepth])
  if (key.includes('fill')) {
    return finiteMax([finiteMax(frame.nodes?.fill_ratio), finiteMax(frame.pipes?.fill_ratio)])
  }
  if (key.includes('velocity') || key.includes('flow')) {
    return finiteMax([finiteMax(frame.pipes?.flow_m3s), finiteMax(frame.ponding?.flow_m3s)])
  }
  if (key.includes('surcharge')) {
    const values = [frame.nodes?.surcharged, frame.pipes?.surcharged]
    return values.reduce<number>(
      (count, series) => count + (series?.filter((value) => value === true).length ?? 0),
      0
    )
  }
  if (key.includes('flood') || key.includes('count')) {
    const values = [frame.nodes?.depth_m, frame.pipes?.depth_m, frame.ponding?.depth_m]
    return values.reduce<number>((count, series) => {
      return count + (series?.filter((value) => typeof value === 'number' && value > 0).length ?? 0)
    }, 0)
  }
  return depth
}

function sampledIndexes(count: number): number[] {
  if (count <= MAX_COMPARISON_SERIES_POINTS)
    return Array.from({ length: count }, (_, index) => index)
  return Array.from({ length: MAX_COMPARISON_SERIES_POINTS }, (_, index) =>
    Math.round((index * (count - 1)) / (MAX_COMPARISON_SERIES_POINTS - 1))
  ).filter((value, index, values) => index === 0 || value !== values[index - 1])
}

/**
 * The comparison endpoint returns aggregate metrics, while the frame endpoints
 * remain the authoritative time series. Load a bounded, real frame sample for
 * both sides so the workbench never renders a synthetic/empty chart.
 */
export async function loadComparisonSeries(
  baselineId: string,
  candidateId: string,
  metricKey?: string,
  signal?: AbortSignal,
  client: SimulationApiClient = simulationApi
): Promise<ReturnType<typeof alignSimulationSeries>> {
  const [baselineIndex, candidateIndex] = await Promise.all([
    client.frames(baselineId, { signal }),
    client.frames(candidateId, { signal }),
  ])
  if (
    baselineIndex.network_revision_id !== candidateIndex.network_revision_id ||
    baselineIndex.target_manifest_sha256 !== candidateIndex.target_manifest_sha256 ||
    baselineIndex.frame_interval_seconds !== candidateIndex.frame_interval_seconds
  ) {
    throw new Error('Comparison frame indexes are not compatible')
  }
  const count = Math.min(baselineIndex.frame_count, candidateIndex.frame_count)
  const indexes = sampledIndexes(count)
  const [baseline, candidate] = await Promise.all([
    loadSeriesFromIndex(client, baselineId, baselineIndex, indexes, metricKey),
    loadSeriesFromIndex(client, candidateId, candidateIndex, indexes, metricKey),
  ])
  return alignSimulationSeries(baseline, candidate)
}

async function loadSeriesFromIndex(
  client: SimulationApiClient,
  simulationId: string,
  index: Awaited<ReturnType<SimulationApiClient['frames']>>,
  indexes: readonly number[],
  metricKey?: string,
  signal?: AbortSignal
): Promise<SimulationSeriesPoint[]> {
  const frames = await Promise.all(
    indexes.map((frameIndex) => client.frame(simulationId, frameIndex, { signal }))
  )
  return frames.map((frame, position) => ({
    timestamp: index.frames[indexes[position]]?.frame_time ?? frame.frame_time,
    value: frameMetric(frame, metricKey),
  }))
}

export const comparisonApi = { loadSeries: loadComparisonSeries }
