import { useMemo, useState } from 'react'
import { Alert, Button, Empty, Select, Space, Tag } from 'antd'
import { useTranslation } from 'react-i18next'
import { ComparisonMetricsTable, DifferenceLegend, SimulationComparisonChart } from '../charts'
import type {
  AlignedSimulationPoint,
  ComparisonMetric,
  ComparisonMetricLabels,
  ComparisonViewMode,
  SimulationComparisonLabels,
  SimulationComparisonResponse,
} from '../comparison'
import type { SimulationTask } from '../model/types'

export interface ComparisonWorkbenchProps {
  tasks: readonly SimulationTask[]
  baseline: SimulationTask | null
  candidate: SimulationTask | null
  comparison: SimulationComparisonResponse | null
  series?: readonly AlignedSimulationPoint[]
  onBaselineChange: (simulationId: string) => void
  onCandidateChange: (simulationId: string) => void
  onRefresh: () => void
  disabled?: boolean
}

const EMPTY_SERIES: AlignedSimulationPoint[] = []

function taskOptions(tasks: readonly SimulationTask[]) {
  return tasks
    .filter((task) => task.status === 'completed' && task.published_attempt_id)
    .map((task) => ({ value: task.id, label: `${task.id.slice(0, 8)} · ${task.solver_id}` }))
}

export function ComparisonWorkbench({
  tasks,
  baseline,
  candidate,
  comparison,
  series = EMPTY_SERIES,
  onBaselineChange,
  onCandidateChange,
  onRefresh,
  disabled = false,
}: ComparisonWorkbenchProps) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<ComparisonViewMode>('overlay')
  const options = useMemo(() => taskOptions(tasks), [tasks])
  const compatible = comparison?.compatible !== false
  const metricLabels: ComparisonMetricLabels = {
    metric: t('comparison.metric'),
    baseline: t('comparison.baseline'),
    candidate: t('comparison.candidate'),
    absoluteDelta: t('comparison.absoluteDelta'),
    relativeDelta: t('comparison.relativeDelta'),
    betterDirection: t('comparison.betterDirection'),
    betterDirections: {
      higher: t('comparison.higher'),
      lower: t('comparison.lower'),
      target: t('comparison.target'),
    },
    unit: t('comparison.unit'),
    notAvailable: t('comparison.notAvailable'),
  }
  const chartLabels: SimulationComparisonLabels = {
    baseline: t('comparison.baseline'),
    candidate: t('comparison.candidate'),
    difference: t('comparison.difference'),
  }
  const metrics = (comparison?.metrics ?? []) as ComparisonMetric[]

  return (
    <section className="simulation-panel" data-testid="simulation-comparison-workbench">
      <header className="simulation-panel__header">
        <div>
          <h2>{t('comparison.title')}</h2>
          <p>{t('comparison.subtitle')}</p>
        </div>
        <Button
          size="small"
          onClick={onRefresh}
          disabled={disabled || !baseline || !candidate}
          data-testid="comparison-refresh"
        >
          {t('comparison.refresh')}
        </Button>
      </header>
      <div className="simulation-toolbar">
        <label>
          <span>{t('comparison.baseline')}</span>
          <Select
            size="small"
            value={baseline?.id}
            options={options}
            placeholder={t('comparison.selectBaseline')}
            onChange={onBaselineChange}
            disabled={disabled}
            aria-label={t('comparison.selectBaseline')}
            data-testid="comparison-baseline-select"
            classNames={{ popup: { root: 'comparison-baseline-dropdown' } }}
          />
        </label>
        <label>
          <span>{t('comparison.candidate')}</span>
          <Select
            size="small"
            value={candidate?.id}
            options={options}
            placeholder={t('comparison.selectCandidate')}
            onChange={onCandidateChange}
            disabled={disabled}
            aria-label={t('comparison.selectCandidate')}
            data-testid="comparison-candidate-select"
            classNames={{ popup: { root: 'comparison-candidate-dropdown' } }}
          />
        </label>
        <Space.Compact role="group" aria-label={t('comparison.mode')}>
          {(['overlay', 'split', 'difference'] as const).map((value) => (
            <Button
              key={value}
              size="small"
              type={mode === value ? 'primary' : 'default'}
              onClick={() => setMode(value)}
              data-testid={`comparison-mode-${value}`}
              disabled={!compatible && value === 'difference'}
            >
              {t(`comparison.modes.${value}`)}
            </Button>
          ))}
        </Space.Compact>
      </div>
      {!compatible ? (
        <Alert
          type="warning"
          showIcon
          message={t('comparison.incompatible')}
          description={comparison?.reason ?? t('comparison.incompatibleDescription')}
        />
      ) : null}
      {comparison ? (
        <>
          <div className="simulation-kpis" data-testid="comparison-kpis">
            <Tag color="cyan">
              {t('comparison.revision')}: {comparison.network_revision_id.slice(0, 8)}
            </Tag>
            <Tag color="purple">
              {t('comparison.frames')}: {comparison.frame_grid.frame_count}
            </Tag>
            <Tag color="blue">
              {t('comparison.manifest')}: {comparison.target_manifest_sha256.slice(0, 12)}
            </Tag>
          </div>
          <ComparisonMetricsTable
            metrics={metrics}
            labels={metricLabels}
            ariaLabel={t('comparison.metricsAria')}
          />
          <DifferenceLegend
            data={series}
            labels={{
              negative: t('comparison.negative'),
              zero: t('comparison.zero'),
              positive: t('comparison.positive'),
            }}
            ariaLabel={t('comparison.differenceAria')}
          />
          <SimulationComparisonChart
            data={series}
            labels={chartLabels}
            mode={mode}
            comparison={comparison}
            ariaLabel={t('comparison.chartAria')}
            height={300}
          />
        </>
      ) : (
        <Empty description={t('comparison.empty')} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </section>
  )
}

export default ComparisonWorkbench
