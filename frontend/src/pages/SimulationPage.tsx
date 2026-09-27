import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './SimulationPage.css'
import { Alert, Button, Empty, Select, Tag, Tooltip } from 'antd'
import {
  AimOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { useTranslation } from 'react-i18next'
import { canWriteSimulation, getStoredAccessToken, getStoredRole } from '@/auth'
import { ForecastStreamBoundary } from '@/features/simulation/integration/ForecastStreamBoundary'
import { HydraulicMapPanel } from '@/features/simulation/integration/HydraulicMapPanel'
import { encodeFrame, frameCacheKey } from '@/features/simulation/map'
import { ComparisonWorkbench } from '@/features/simulation/integration/ComparisonWorkbench'
import { ObservedPredictedChart } from '@/features/simulation/charts/ObservedPredictedChart'
import {
  useHydraulicWorkspace,
  type SimulationWorkspaceState,
} from '@/features/simulation/integration/useHydraulicWorkspace'
import { rainfallScenarioApi } from '@/features/simulation/integration/rainfallApi'
import { loadObservedComparison } from '@/features/simulation/integration/observedApi'
import { loadComparisonSeries } from '@/features/simulation/integration/comparisonApi'
import { useHydraulicRealtime } from '@/features/simulation/realtime/HydraulicRealtimeProvider'
import { ForecastRiskCard } from '@/components/layout/ForecastRiskCard'
import { SimulationTaskPanel } from '@/features/simulation/tasks/TaskPanel'
import { useSimulationTask } from '@/features/simulation/hooks/useSimulationTask'
import { type TimelineSpeed } from '@/features/simulation/hooks/useTimelinePlayer'
import { TimelineControl } from '@/features/simulation/timeline/TimelineControl'
import { DepthLegend } from '@/features/simulation/timeline/DepthLegend'
import { RainfallScenarioEditor } from '@/features/simulation/rainfall/RainfallScenarioEditor'
import type { RainfallScenario } from '@/features/simulation/rainfall/model'
import type { SimulationComparisonResponse } from '@/features/simulation/comparison'
import type {
  AlignedSimulationPoint,
  ObservedPredictedPoint,
} from '@/features/simulation/comparison'
import type {
  SimulationFrame,
  SimulationTask,
  SimulationSubmitRequest,
} from '@/features/simulation/model/types'
import { simulationApi } from '@/features/simulation/api'

const TILE_LAYER_OPTIONS = [
  { value: 'fill_ratio' as const, labelKey: 'map.metrics.fill_ratio' },
  { value: 'depth_m' as const, labelKey: 'map.metrics.depth_m' },
  { value: 'velocity_mps' as const, labelKey: 'map.metrics.velocity_mps' },
]

function buildSubmitRequest(
  networkId: string,
  revisionId: string,
  rainfallScenarioId: string,
  parameterSetId: string,
  startAt: string,
  durationSeconds: number
): SimulationSubmitRequest {
  return {
    network_id: networkId,
    network_revision_id: revisionId,
    rainfall_scenario_id: rainfallScenarioId,
    parameter_set_id: parameterSetId,
    initial_state_id: null,
    start_at: startAt,
    duration_seconds: durationSeconds,
    frame_interval_seconds: 300,
    ponding_enabled: true,
    strict_stability: true,
    solver_selection: 'auto',
  }
}

function buildDifferenceFrame(
  baseline: SimulationFrame,
  candidate: SimulationFrame
): ReturnType<typeof encodeFrame> | null {
  if (
    baseline.network_revision_id !== candidate.network_revision_id ||
    baseline.target_manifest_id !== candidate.target_manifest_id ||
    baseline.target_manifest_sha256 !== candidate.target_manifest_sha256 ||
    baseline.frame_index !== candidate.frame_index
  ) {
    return null
  }
  const subtractLayer = (
    left: Record<string, unknown> | undefined,
    right: Record<string, unknown> | undefined,
    fields: readonly string[]
  ) => {
    if (
      !left ||
      !right ||
      !Array.isArray(left.feature_index) ||
      !Array.isArray(right.feature_index)
    ) {
      return undefined
    }
    const leftIndexes = left.feature_index as number[]
    const leftValues = new Map(
      leftIndexes.map((index, position) => [
        index,
        fields.map((field) => {
          const values = left[field]
          return Array.isArray(values) ? values[position] : undefined
        }),
      ])
    )
    const indexes = (right.feature_index as number[]).filter((index) => leftValues.has(index))
    const result: Record<string, unknown> = { feature_index: indexes }
    for (const field of fields) {
      const rightValues = right[field]
      if (!Array.isArray(rightValues)) continue
      result[field] = indexes.map((index) => {
        const leftValue = leftValues.get(index)?.[fields.indexOf(field)]
        const rightValue = rightValues[(right.feature_index as number[]).indexOf(index)]
        return typeof leftValue === 'number' && typeof rightValue === 'number'
          ? Math.abs(rightValue - leftValue)
          : null
      })
    }
    return result
  }
  const nodes = subtractLayer(
    baseline.nodes as Record<string, unknown> | undefined,
    candidate.nodes as Record<string, unknown> | undefined,
    ['depth_m', 'fill_ratio']
  )
  const pipes = subtractLayer(
    baseline.pipes as Record<string, unknown> | undefined,
    candidate.pipes as Record<string, unknown> | undefined,
    ['fill_ratio', 'flow_m3s', 'depth_m']
  )
  const ponding = subtractLayer(
    baseline.ponding as Record<string, unknown> | undefined,
    candidate.ponding as Record<string, unknown> | undefined,
    ['depth_m', 'flow_m3s']
  )
  const key = frameCacheKey(
    candidate.simulation_id,
    candidate.frame_index,
    'difference',
    candidate.network_revision_id
  )
  const withoutTransportHash = { ...candidate }
  delete withoutTransportHash.checksum
  delete withoutTransportHash.payload_sha256
  try {
    return encodeFrame(key, {
      ...withoutTransportHash,
      ...(nodes ? { nodes: nodes as unknown as SimulationFrame['nodes'] } : {}),
      ...(pipes ? { pipes: pipes as unknown as SimulationFrame['pipes'] } : {}),
      ...(ponding ? { ponding: ponding as unknown as SimulationFrame['ponding'] } : {}),
    })
  } catch {
    return null
  }
}

function SimulationPageContent({ workspace }: { workspace: SimulationWorkspaceState }) {
  const { t } = useTranslation()
  const token = getStoredAccessToken() ?? ''
  const role = getStoredRole()
  const canWrite = canWriteSimulation(role)
  const sharedRealtime = useHydraulicRealtime()
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [baselineId, setBaselineId] = useState<string | null>(null)
  const [candidateId, setCandidateId] = useState<string | null>(null)
  const [comparison, setComparison] = useState<SimulationComparisonResponse | null>(null)
  const [comparisonSeries, setComparisonSeries] = useState<AlignedSimulationPoint[]>([])
  const [comparisonMapMode, setComparisonMapMode] = useState<
    'selected' | 'baseline' | 'candidate' | 'difference'
  >('selected')
  const [comparisonFrames, setComparisonFrames] = useState<{
    baseline: SimulationFrame | null
    candidate: SimulationFrame | null
  }>({ baseline: null, candidate: null })
  const [rainfallScenarios, setRainfallScenarios] = useState<RainfallScenario[]>([])
  const [rainfallScenarioId, setRainfallScenarioId] = useState<string | null>(null)
  const [rainfallDraft, setRainfallDraft] = useState<RainfallScenario | undefined>()
  const [rainfallMessage, setRainfallMessage] = useState<string | null>(null)
  const [startAt, setStartAt] = useState(() => new Date().toISOString())
  const [durationHours, setDurationHours] = useState(24)
  const [timelineFrameIndex, setTimelineFrameIndex] = useState(0)
  const [observedStatus, setObservedStatus] = useState<string | null>(null)
  const [observedSeries, setObservedSeries] = useState<ObservedPredictedPoint[]>([])
  const [comparisonError, setComparisonError] = useState<string | null>(null)
  const comparisonAbortRef = useRef<AbortController | null>(null)
  const knownTaskIdsRef = useRef(new Set<string>())
  const initialFrameTaskRef = useRef<string | null>(null)
  const { loadFrame, selectedSimulationId, selectSimulation } = workspace
  const taskController = useSimulationTask({
    networkId: workspace.selectedNetworkId ?? '',
    token,
    stream: sharedRealtime,
  })
  const tasks = taskController.tasks
  const refreshTask = taskController.refreshTask

  useEffect(() => {
    knownTaskIdsRef.current = new Set(tasks.map((task) => task.id))
    if (!selectedTaskId || !tasks.some((task) => task.id === selectedTaskId)) {
      setSelectedTaskId(tasks[0]?.id ?? null)
    }
  }, [selectedTaskId, tasks])

  useEffect(() => {
    return sharedRealtime.subscribeTask((event) => {
      const simulationId = event.simulation_id ?? event.simulation?.id ?? event.task?.id
      // Forecast events are delivered on a separate listener and must never
      // select a task. Only known task identities can trigger REST refresh.
      if (simulationId && knownTaskIdsRef.current.has(simulationId)) {
        void refreshTask(simulationId)
      }
    })
  }, [refreshTask, sharedRealtime])

  useEffect(() => {
    if (!workspace.selectedNetworkId) {
      setRainfallScenarios([])
      setRainfallScenarioId(null)
      setRainfallDraft(undefined)
      return
    }
    let active = true
    void rainfallScenarioApi
      .list(workspace.selectedNetworkId)
      .then((items) => {
        if (!active) return
        setRainfallScenarios(items)
        setRainfallScenarioId(items[0]?.id ?? null)
        setRainfallDraft(items[0])
      })
      .catch((error: unknown) => {
        if (active) setRainfallMessage(error instanceof Error ? error.message : String(error))
      })
    return () => {
      active = false
    }
  }, [workspace.selectedNetworkId])

  useEffect(() => {
    const task = tasks.find((candidate) => candidate.id === selectedTaskId)
    if (!task || task.status !== 'completed' || !workspace.selectedNetworkId) {
      setObservedStatus(null)
      setObservedSeries([])
      return
    }
    const controller = new AbortController()
    setObservedSeries([])
    void loadObservedComparison(task.id, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setObservedStatus(result.status)
          setObservedSeries(result.pairs)
        }
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setObservedStatus('not_ready')
          setObservedSeries([])
        }
      })
    return () => controller.abort()
  }, [selectedTaskId, tasks, workspace.selectedNetworkId])

  const selectedTask = tasks.find((task) => task.id === selectedTaskId) ?? null
  const baselineTask = tasks.find((task) => task.id === baselineId) ?? null
  const candidateTask = tasks.find((task) => task.id === candidateId) ?? null

  const selectTask = useCallback(
    (task: SimulationTask) => {
      setSelectedTaskId(task.id)
      setTimelineFrameIndex(0)
      initialFrameTaskRef.current = null
      selectSimulation(task.id)
    },
    [selectSimulation]
  )

  useEffect(() => {
    initialFrameTaskRef.current = null
  }, [selectedTaskId])

  useEffect(() => {
    if (!selectedTask || selectedSimulationId === selectedTask.id) return
    selectSimulation(selectedTask.id)
    setTimelineFrameIndex(0)
  }, [selectedTask, selectSimulation, selectedSimulationId])

  useEffect(() => {
    if (
      !selectedTaskId ||
      selectedSimulationId !== selectedTaskId ||
      initialFrameTaskRef.current === selectedTaskId
    ) {
      return
    }
    initialFrameTaskRef.current = selectedTaskId
    void loadFrame(0)
  }, [loadFrame, selectedSimulationId, selectedTaskId])

  const loadComparison = useCallback(async () => {
    comparisonAbortRef.current?.abort()
    const controller = new AbortController()
    comparisonAbortRef.current = controller
    if (!baselineId || !candidateId || baselineId === candidateId) {
      setComparison(null)
      setComparisonSeries([])
      return
    }
    setComparisonError(null)
    try {
      const result = await simulationApi.compare(baselineId, candidateId, undefined, {
        signal: controller.signal,
      })
      if (controller.signal.aborted) return
      setComparison(result as unknown as SimulationComparisonResponse)
      try {
        const series = await loadComparisonSeries(
          baselineId,
          candidateId,
          result.metrics[0]?.key,
          controller.signal
        )
        if (!controller.signal.aborted) setComparisonSeries(series)
      } catch (seriesError) {
        if (!controller.signal.aborted) {
          setComparisonSeries([])
          setComparisonError(
            seriesError instanceof Error ? seriesError.message : String(seriesError)
          )
        }
      }
    } catch (error) {
      if (controller.signal.aborted) return
      setComparison(null)
      setComparisonSeries([])
      setComparisonError(error instanceof Error ? error.message : String(error))
    }
  }, [baselineId, candidateId])

  useEffect(() => {
    return () => comparisonAbortRef.current?.abort()
  }, [])

  useEffect(() => {
    if (!baselineId || !candidateId || !comparison?.compatible) return
    const maxIndex = Math.max(0, comparison.frame_grid.frame_count - 1)
    const index = Math.min(timelineFrameIndex, maxIndex)
    const controller = new AbortController()
    void Promise.all([
      simulationApi.frame(baselineId, index, { signal: controller.signal }),
      simulationApi.frame(candidateId, index, { signal: controller.signal }),
    ])
      .then(([baseline, candidate]) => {
        if (!controller.signal.aborted) setComparisonFrames({ baseline, candidate })
      })
      .catch(() => {
        if (!controller.signal.aborted) setComparisonFrames({ baseline: null, candidate: null })
      })
    return () => controller.abort()
  }, [baselineId, candidateId, comparison, timelineFrameIndex])

  const parameterSetId = useMemo(() => {
    const networkId = workspace.selectedNetwork?.id
    const revisionId = workspace.selectedNetwork?.network_revision_id
    if (!networkId || !revisionId) return undefined
    return (
      workspace.selectedNetwork?.parameter_set_id ??
      tasks.find(
        (task) =>
          task.network_id === networkId &&
          task.network_revision_id === revisionId &&
          (task.parameter_set_id || task.request?.parameter_set_id)
      )?.parameter_set_id ??
      tasks.find(
        (task) =>
          task.network_id === networkId &&
          task.network_revision_id === revisionId &&
          task.request?.parameter_set_id
      )?.request?.parameter_set_id
    )
  }, [tasks, workspace.selectedNetwork])

  const rainfallIdempotencyKey = useMemo(() => {
    const network = workspace.selectedNetworkId ?? 'network'
    const seed = rainfallDraft
      ? `${rainfallDraft.name}:${rainfallDraft.duration_seconds}:${rainfallDraft.random_seed}:${rainfallDraft.samples.length}`
      : 'draft'
    return `rainfall-${network}-${seed}`.slice(0, 128)
  }, [rainfallDraft, workspace.selectedNetworkId])

  const submitRequest = useMemo(() => {
    if (!workspace.selectedNetwork || !rainfallScenarioId || !parameterSetId) return undefined
    return buildSubmitRequest(
      workspace.selectedNetwork.id,
      workspace.selectedNetwork.network_revision_id,
      rainfallScenarioId,
      parameterSetId,
      startAt,
      Math.max(300, Math.round(durationHours * 3_600))
    )
  }, [durationHours, parameterSetId, rainfallScenarioId, startAt, workspace.selectedNetwork])

  const currentFrameIndex =
    workspace.currentFrame && 'frame_index' in workspace.currentFrame
      ? workspace.currentFrame.frame_index
      : workspace.currentFrame?.frameIndex ?? timelineFrameIndex
  const currentFrameChecksum =
    workspace.frameIndex?.frames[currentFrameIndex]?.checksum ??
    (workspace.currentFrame && 'checksum' in workspace.currentFrame
      ? workspace.currentFrame.checksum ?? null
      : null)
  const differenceFrame = useMemo(
    () =>
      comparisonFrames.baseline && comparisonFrames.candidate
        ? buildDifferenceFrame(comparisonFrames.baseline, comparisonFrames.candidate)
        : null,
    [comparisonFrames]
  )
  const comparisonMapFrame =
    comparisonMapMode === 'baseline'
      ? comparisonFrames.baseline
      : comparisonMapMode === 'candidate'
        ? comparisonFrames.candidate
        : comparisonMapMode === 'difference'
          ? differenceFrame
          : workspace.currentFrame
  const comparisonMapFrameIndex =
    comparisonMapFrame && 'frame_index' in comparisonMapFrame
      ? comparisonMapFrame.frame_index
      : comparisonMapFrame?.frameIndex ?? timelineFrameIndex
  const isDerivedDifference = comparisonMapMode === 'difference' && differenceFrame !== null
  const comparisonMapChecksum = isDerivedDifference
    ? null
    : comparisonMapFrame
      ? 'checksum' in comparisonMapFrame
        ? comparisonMapFrame.checksum ?? null
        : 'payload_sha256' in comparisonMapFrame
          ? comparisonMapFrame.payload_sha256 ?? null
          : null
      : null
  const comparisonMapCacheKey = isDerivedDifference
    ? differenceFrame?.key ?? null
    : comparisonMapFrame
      ? frameCacheKey(
          'simulation_id' in comparisonMapFrame
            ? comparisonMapFrame.simulation_id
            : comparisonMapFrame.simulationId,
          comparisonMapFrameIndex,
          undefined,
          'network_revision_id' in comparisonMapFrame
            ? comparisonMapFrame.network_revision_id
            : comparisonMapFrame.networkRevisionId
        )
      : null

  if (!workspace.selectedNetworkId && !workspace.networkLoading) {
    return (
      <div className="simulation-page">
        <Empty description={t('simulation.noNetwork')} />
      </div>
    )
  }

  return (
    <div className="simulation-page" data-testid="simulation-page">
      <header className="simulation-hero">
        <div>
          <div className="simulation-eyebrow">
            <ExperimentOutlined /> {t('simulation.eyebrow')}
          </div>
          <h1>{t('simulation.title')}</h1>
          <p>{t('simulation.subtitle')}</p>
        </div>
        <div className="simulation-hero__status">
          <Tag color={sharedRealtime.status === 'connected' ? 'green' : 'orange'}>
            {t(`simulation.stream.${sharedRealtime.status}`)}
          </Tag>
          <Tag color={canWrite ? 'cyan' : 'default'}>
            {t(`simulation.role.${role ?? 'viewer'}`)}
          </Tag>
        </div>
      </header>

      <section className="simulation-control-strip" aria-label={t('simulation.workspace')}>
        <label>
          <span>{t('network.label')}</span>
          <Select
            value={workspace.selectedNetworkId ?? undefined}
            loading={workspace.networkLoading}
            options={workspace.networks.map((network) => ({
              value: network.id,
              label: network.name ?? network.id,
            }))}
            onChange={workspace.selectNetwork}
            aria-label={t('network.label')}
            data-testid="simulation-network-select"
          />
        </label>
        <label>
          <span>{t('simulation.colorMetric')}</span>
          <Select
            value={workspace.colorMetric}
            options={TILE_LAYER_OPTIONS.map((option) => ({
              value: option.value,
              label: t(option.labelKey),
            }))}
            onChange={workspace.setColorMetric}
            aria-label={t('simulation.colorMetric')}
          />
        </label>
        <div className="simulation-layer-toggles" role="group" aria-label={t('map.layersTitle')}>
          {(['nodes', 'pipes', 'catchments', 'ponding'] as const).map((layer) => (
            <Button
              key={layer}
              size="small"
              type={workspace.layerVisibility[layer] ? 'primary' : 'default'}
              onClick={() => workspace.setLayerVisibility(layer, !workspace.layerVisibility[layer])}
            >
              {t(`map.layers.${layer}`)}
            </Button>
          ))}
        </div>
        <Button
          size="small"
          icon={<ReloadOutlined />}
          onClick={() => void workspace.refreshMap()}
          loading={workspace.mapLoading}
        >
          {t('simulation.refreshMap')}
        </Button>
      </section>

      {workspace.networkError || workspace.mapError ? (
        <div className="simulation-error" role="alert">
          {workspace.networkError ?? workspace.mapError}
        </div>
      ) : null}

      <div className="simulation-layout">
        <section className="simulation-map-column">
          <div className="simulation-panel simulation-panel--map">
            <div className="simulation-panel__header">
              <div>
                <h2>
                  <AimOutlined /> {t('map.title')}
                </h2>
                <p>{t('map.subtitle')}</p>
              </div>
              {workspace.renderStats ? (
                <Tag
                  color="cyan"
                  data-testid="simulation-render-stats"
                  data-drawn={Object.values(workspace.renderStats.drawn).reduce(
                    (total, count) => total + count,
                    0
                  )}
                >
                  {t('map.renderStats', {
                    drawn: Object.values(workspace.renderStats.drawn).reduce((a, b) => a + b, 0),
                    ms: workspace.renderStats.renderMs.toFixed(1),
                  })}
                </Tag>
              ) : null}
            </div>
            <div className="simulation-map-modes" role="group" aria-label={t('comparison.mapMode')}>
              {(['selected', 'baseline', 'candidate', 'difference'] as const).map((mode) => (
                <Button
                  key={mode}
                  size="small"
                  type={comparisonMapMode === mode ? 'primary' : 'default'}
                  onClick={() => setComparisonMapMode(mode)}
                  disabled={
                    mode !== 'selected' &&
                    (!baselineTask || !candidateTask || !comparison?.compatible)
                  }
                  data-testid={`simulation-map-mode-${mode}`}
                >
                  {t(`comparison.mapModes.${mode}`)}
                </Button>
              ))}
            </div>
            {workspace.mapData && !workspace.mapData.complete ? (
              <Alert
                type="warning"
                showIcon
                role="status"
                message={t('map.partialTitle')}
                description={t('map.partialDescription', {
                  layers: workspace.mapData.truncated_layers.join(', '),
                  returned: Object.values(workspace.mapData.returned_counts).reduce(
                    (a, b) => a + b,
                    0
                  ),
                  source: Object.values(workspace.mapData.source_counts).reduce((a, b) => a + b, 0),
                })}
              />
            ) : null}
            <div
              className="simulation-map-frame"
              data-testid="simulation-map-frame"
              data-comparison-mode={comparisonMapMode}
            >
              {workspace.selectedNetwork ? (
                <HydraulicMapPanel
                  networkId={workspace.selectedNetwork.id}
                  networkRevisionId={workspace.selectedNetwork.network_revision_id}
                  mapData={workspace.mapData}
                  frame={comparisonMapFrame}
                  frameCacheKey={comparisonMapCacheKey}
                  frameChecksum={comparisonMapChecksum ?? currentFrameChecksum}
                  derivedFrame={isDerivedDifference}
                  colorMetric={workspace.colorMetric}
                  layerVisibility={workspace.layerVisibility}
                  onStats={workspace.setRenderStats}
                  onFrameError={(_errors) => workspace.setLayerVisibility('ponding', true)}
                />
              ) : (
                <Empty description={t('simulation.noNetwork')} />
              )}
            </div>
            <div className="simulation-map-legend">
              <DepthLegend
                title={t(`map.legend.${workspace.colorMetric}`)}
                unit={
                  workspace.colorMetric === 'velocity_mps'
                    ? 'm/s'
                    : workspace.colorMetric === 'fill_ratio'
                      ? 'ratio'
                      : 'm'
                }
                stops={
                  workspace.colorMetric === 'fill_ratio'
                    ? [
                        { min: 0, max: 0.25, color: '#1E3A5F' },
                        { min: 0.25, max: 0.5, color: '#0E7490' },
                        { min: 0.5, max: 0.75, color: '#f59e0b' },
                        { min: 0.75, max: Infinity, color: '#ff3366' },
                      ]
                    : workspace.colorMetric === 'velocity_mps'
                      ? [
                          { min: 0, max: 0.5, color: '#1E3A5F' },
                          { min: 0.5, max: 1, color: '#0E7490' },
                          { min: 1, max: 2, color: '#f59e0b' },
                          { min: 2, max: Infinity, color: '#ff3366' },
                        ]
                      : [
                          { min: 0, max: 0.05, color: '#1E3A5F' },
                          { min: 0.05, max: 0.15, color: '#0E7490' },
                          { min: 0.15, max: 0.3, color: '#f59e0b' },
                          { min: 0.3, max: Infinity, color: '#ff3366' },
                        ]
                }
              />
            </div>
          </div>

          <div className="simulation-panel">
            <TimelineControl
              frameCount={workspace.frameIndex?.frame_count ?? 0}
              frameTimes={workspace.frameIndex?.frames.map((frame) => frame.frame_time) ?? []}
              labels={{
                title: t('timeline.title'),
                play: t('timeline.play'),
                pause: t('timeline.pause'),
                playbackSpeed: t('timeline.playbackSpeed'),
                framesPerSecond: (speed: TimelineSpeed) => t('timeline.framesPerSecond', { speed }),
                seek: t('timeline.seek'),
                currentTime: t('timeline.currentTime'),
                framePosition: (index, count) => t('timeline.framePosition', { index, count }),
                frameInterval: (minutes) => t('timeline.frameInterval', { minutes }),
                pageHidden: t('timeline.pageHidden'),
                playbackUnavailable: t('timeline.playbackUnavailable'),
                dataInvalid: t('timeline.dataInvalid'),
              }}
              initialFrameIndex={timelineFrameIndex}
              initialSpeed={1}
              playbackDisabled={Boolean(workspace.frameError)}
              prefetchEnabled={Boolean(selectedTaskId)}
              prefetchFrame={(index, signal) => workspace.prefetchFrame(index, signal)}
              timelineKey={`${selectedTaskId ?? ''}/${workspace.frameIndex?.published_attempt_id ?? ''}`}
              onFrameChange={(index) => {
                setTimelineFrameIndex(index)
                if (selectedTaskId) void loadFrame(index)
              }}
              onPrefetchError={() => workspace.setLayerVisibility('ponding', true)}
            />
            {workspace.frameLoading ? (
              <p className="simulation-status">{t('timeline.buffering')}</p>
            ) : null}
            {workspace.frameError ? (
              <p className="simulation-error" role="alert">
                {workspace.frameError}
              </p>
            ) : null}
          </div>

          <ComparisonWorkbench
            tasks={tasks}
            baseline={baselineTask}
            candidate={candidateTask}
            comparison={comparison}
            series={comparisonSeries}
            onBaselineChange={setBaselineId}
            onCandidateChange={setCandidateId}
            onRefresh={() => void loadComparison()}
            disabled={!canWrite && false}
          />
          {comparisonError ? (
            <p className="simulation-error" role="alert">
              {comparisonError}
            </p>
          ) : null}
        </section>

        <aside className="simulation-side-column">
          <ForecastRiskCard networkId={workspace.selectedNetworkId} withProvider={false} />
          <SimulationTaskPanel
            networkId={workspace.selectedNetworkId ?? ''}
            token={token}
            canWrite={canWrite}
            submitRequest={submitRequest}
            stream={sharedRealtime}
            controller={taskController}
            onTaskSelect={selectTask}
          />
          <section className="simulation-panel">
            <div className="simulation-panel__header">
              <div>
                <h2>{t('rainfall.title')}</h2>
                <p>{t('rainfall.subtitle')}</p>
              </div>
              <Select
                size="small"
                value={rainfallScenarioId ?? undefined}
                options={rainfallScenarios.map((scenario) => ({
                  value: scenario.id,
                  label: scenario.name,
                }))}
                onChange={(value) => {
                  setRainfallScenarioId(value)
                  setRainfallDraft(rainfallScenarios.find((scenario) => scenario.id === value))
                }}
                placeholder={t('rainfall.select')}
                aria-label={t('rainfall.select')}
              />
            </div>
            <RainfallScenarioEditor
              key={rainfallDraft?.id ?? 'rainfall-draft'}
              initialScenario={rainfallDraft}
              networkId={workspace.selectedNetworkId ?? undefined}
              idempotencyKey={rainfallIdempotencyKey}
              disabled={!canWrite || !workspace.selectedNetworkId}
              onCreateScenario={async (command) => {
                if (!workspace.selectedNetworkId) return
                const created = await rainfallScenarioApi.create(command)
                setRainfallScenarios((current) => [created, ...current])
                setRainfallScenarioId(created.id ?? null)
                setRainfallMessage(t('rainfall.created'))
              }}
              onChange={setRainfallDraft}
            />
            {rainfallMessage ? (
              <p className="simulation-status" role="status" data-testid="rainfall-created">
                {rainfallMessage}
              </p>
            ) : null}
          </section>
          <section className="simulation-panel simulation-observed-panel">
            <div className="simulation-panel__header">
              <div>
                <h2>{t('forecast.observedTitle')}</h2>
                <p>{t('forecast.observedSubtitle')}</p>
              </div>
              {selectedTask ? <Tag>{selectedTask.status}</Tag> : null}
            </div>
            <ObservedPredictedChart
              data={observedSeries}
              labels={{
                observed: t('forecast.observed'),
                predicted: t('forecast.predicted'),
                uncertainty: t('forecast.uncertainty'),
                issuedAt: t('forecast.issuedAt'),
              }}
              issueTime={observedSeries[0]?.timestamp ?? null}
              ariaLabel={t('forecast.observedChartAria')}
              height={240}
            />
            <p className="simulation-status" data-testid="simulation-observed-status">
              {observedStatus
                ? t(`forecast.observedStatus.${observedStatus}`)
                : t('forecast.observedEmpty')}
            </p>
          </section>
          <section className="simulation-panel">
            <div className="simulation-panel__header">
              <div>
                <h2>{t('simulation.submitTitle')}</h2>
                <p>{t('simulation.submitSubtitle')}</p>
              </div>
              <Tooltip title={canWrite ? t('simulation.submit') : t('simulation.readOnly')}>
                <span>
                  <Button
                    type="primary"
                    size="small"
                    icon={<PlayCircleOutlined />}
                    disabled={!canWrite || !submitRequest}
                    data-testid="simulation-submit"
                    onClick={() => {
                      if (submitRequest) void taskController.submit(submitRequest)
                    }}
                  >
                    {t('simulation.submit')}
                  </Button>
                </span>
              </Tooltip>
            </div>
            <div className="simulation-form-grid">
              <label>
                <span>{t('simulation.startAt')}</span>
                <input
                  type="datetime-local"
                  value={startAt.slice(0, 16)}
                  onChange={(event) => setStartAt(new Date(event.target.value).toISOString())}
                  disabled={!canWrite}
                />
              </label>
              <label>
                <span>{t('simulation.durationHours')}</span>
                <input
                  type="number"
                  min={1}
                  max={72}
                  value={durationHours}
                  onChange={(event) => setDurationHours(Number(event.target.value))}
                  disabled={!canWrite}
                />
              </label>
            </div>
            {!parameterSetId ? (
              <p className="simulation-status" role="status">
                {t('simulation.parameterSetMissing')}
              </p>
            ) : null}
            <p className="simulation-status">
              {t('simulation.roleHint', { role: role ?? 'viewer' })}
            </p>
          </section>
        </aside>
      </div>
    </div>
  )
}

export default function SimulationPage() {
  const workspace = useHydraulicWorkspace()
  return (
    <ForecastStreamBoundary networkId={workspace.selectedNetworkId ?? ''}>
      <SimulationPageContent workspace={workspace} />
    </ForecastStreamBoundary>
  )
}
