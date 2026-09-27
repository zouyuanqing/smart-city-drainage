import {
  CloseCircleOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RetweetOutlined,
} from '@ant-design/icons'
import { Alert, Button, Progress, Tag, Tooltip } from 'antd'
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  useSimulationTask,
  type SharedSimulationStream,
  type UseSimulationTaskResult,
} from '../hooks/useSimulationTask'
import { isManuallyRetryable } from './taskState'
import type {
  SimulationSubmitRequest,
  SimulationTask,
  SimulationTaskClient,
  SimulationTaskFilter,
} from './types'
import './TaskPanel.css'

export interface SimulationTaskPanelProps {
  networkId: string
  token?: string | null
  client?: SimulationTaskClient
  canWrite?: boolean
  submitRequest?: SimulationSubmitRequest
  enableStream?: boolean
  stream?: SharedSimulationStream
  streamEndpoint?: string | ((networkId: string) => string)
  streamFetchImpl?: typeof fetch
  pollIntervalMs?: number
  hiddenPollIntervalMs?: number
  connectedPollIntervalMs?: number
  pageSize?: number
  keyFactory?: () => string
  resolveRetryRequest?: (task: SimulationTask) => SimulationSubmitRequest | undefined
  onTaskSelect?: (task: SimulationTask) => void
  /** Reuse a page-owned controller so polling and state have one source of truth. */
  controller?: UseSimulationTaskResult
  className?: string
}

const FILTERS: SimulationTaskFilter[] = [
  'all',
  'active',
  'queued',
  'leased',
  'running',
  'retry_wait',
  'cancelling',
  'completed',
  'failed',
  'timed_out',
  'cancelled',
]

function formatTimestamp(value: string | null | undefined, locale?: string): string {
  if (!value) return '—'
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return value
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'short',
    timeStyle: 'medium',
  }).format(timestamp)
}

function statusColor(status: SimulationTask['status']): string {
  switch (status) {
    case 'completed':
      return 'success'
    case 'failed':
    case 'timed_out':
      return 'error'
    case 'cancelling':
    case 'cancelled':
      return 'default'
    case 'retry_wait':
      return 'warning'
    default:
      return 'processing'
  }
}

function shortKey(key: string | null): string {
  if (!key) return '—'
  return key.length <= 14 ? key : `${key.slice(0, 10)}…${key.slice(-4)}`
}

function SimulationTaskPanelView({
  canWrite = false,
  submitRequest,
  onTaskSelect,
  controller: controllerProp,
  className,
}: SimulationTaskPanelProps & { controller: UseSimulationTaskResult }) {
  const { t, i18n } = useTranslation()
  const controller = controllerProp
  const locale = i18n.language?.startsWith('zh') ? 'zh-CN' : 'en-US'
  const label = createTaskLabel(t)
  const filterOptions = useMemo(
    () =>
      FILTERS.map((filter) => ({
        value: filter,
        label: t(`tasks.status.${filter}`, { defaultValue: filter }),
      })),
    [t]
  )

  const handleSubmit = async () => {
    if (!submitRequest) return
    try {
      await controller.submit(submitRequest)
    } catch {
      // The controller retains the idempotency key and exposes the structured action error.
    }
  }

  const handleCancel = async (simulationId: string) => {
    try {
      await controller.cancel(simulationId)
    } catch {
      // The controller refreshes the authoritative terminal state for cancel races.
    }
  }

  const handleRetry = async (simulationId: string) => {
    try {
      await controller.retryFailed(simulationId)
    } catch {
      // Missing original inputs and API errors are rendered by the controller.
    }
  }

  const streamConnected = controller.streamStatus === 'connected'
  const receiptState = label(`idempotency.${controller.idempotencyReceipt.state}`)

  return (
    <section
      className={['simulation-task-panel', className].filter(Boolean).join(' ')}
      aria-label={label('title')}
    >
      <header className="simulation-task-panel__header">
        <div>
          <h2 className="simulation-task-panel__title">{label('title')}</h2>
          <p className="simulation-task-panel__summary">
            {label('activeSummary', { count: controller.activeTasks.length })}
          </p>
        </div>
        <div className="simulation-task-panel__sync" role="status">
          <span
            className={`simulation-task-panel__sync-dot${
              streamConnected ? '' : ' simulation-task-panel__sync-dot--fallback'
            }`}
            aria-hidden="true"
          />
          <span>{streamConnected ? label('streamConnected') : label('pollingFallback')}</span>
        </div>
      </header>

      <div className="simulation-task-panel__toolbar">
        <div className="simulation-task-panel__actions">
          <Tooltip title={submitRequest ? undefined : label('submitUnavailable')}>
            <Button
              type="primary"
              size="small"
              icon={<PlayCircleOutlined />}
              disabled={!canWrite || !submitRequest || controller.submitting}
              loading={controller.submitting}
              onClick={() => void handleSubmit()}
            >
              {label('submit')}
            </Button>
          </Tooltip>
          <Button
            size="small"
            icon={<ReloadOutlined />}
            loading={controller.refreshing}
            onClick={() => void controller.refresh()}
          >
            {t('common.refresh', { defaultValue: 'Refresh' })}
          </Button>
        </div>
        <label>
          <span className="simulation-task-panel__summary"> {label('filter')}</span>
          <select
            className="simulation-task-panel__filter"
            value={controller.statusFilter}
            aria-label={label('filter')}
            onChange={(event) =>
              controller.setStatusFilter(event.target.value as SimulationTaskFilter)
            }
          >
            {filterOptions.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {controller.idempotencyReceipt.state !== 'idle' ? (
        <div className="simulation-task-panel__alerts">
          <div
            className="simulation-task-panel__receipt"
            data-testid="idempotency-receipt"
            data-state={controller.idempotencyReceipt.state}
          >
            <Tag color={controller.idempotencyReceipt.state === 'conflict' ? 'error' : 'cyan'}>
              {receiptState}
            </Tag>
            <span>{label('idempotencyKey')}</span>
            <code title={controller.idempotencyReceipt.key ?? undefined}>
              {shortKey(controller.idempotencyReceipt.key)}
            </code>
            {controller.idempotencyReceipt.taskId ? (
              <span>→ {controller.idempotencyReceipt.taskId}</span>
            ) : null}
          </div>
        </div>
      ) : null}

      {controller.error || controller.actionError ? (
        <div className="simulation-task-panel__alerts">
          {controller.error ? (
            <Alert
              type={controller.isStale ? 'warning' : 'error'}
              showIcon
              message={controller.isStale ? label('staleData') : label('loadError')}
              description={controller.error}
            />
          ) : null}
          {controller.actionError ? (
            <Alert
              type="error"
              showIcon
              closable
              message={label('actionError')}
              description={controller.actionError}
              onClose={controller.clearActionError}
            />
          ) : null}
        </div>
      ) : null}

      {controller.loading ? (
        <p className="simulation-task-panel__empty">{label('loading')}</p>
      ) : controller.filteredTasks.length === 0 ? (
        <p className="simulation-task-panel__empty" data-testid="task-empty">
          {label('empty')}
        </p>
      ) : (
        <ol className="simulation-task-panel__list" aria-label={label('history')}>
          {controller.filteredTasks.map((task) => {
            const cancellationPending = controller.pendingCancellationIds.includes(task.id)
            const retryPending = controller.pendingRetryIds.includes(task.id)
            const cancellable =
              task.status !== 'cancelling' &&
              (task.status === 'queued' ||
                task.status === 'leased' ||
                task.status === 'running' ||
                task.status === 'retry_wait')
            const retryable = isManuallyRetryable(task)
            const retryHasRequest = Boolean(task.request)
            return (
              <li
                key={task.id}
                className="simulation-task-panel__row"
                data-testid="simulation-task-row"
                data-status={task.status}
              >
                <div className="simulation-task-panel__row-top">
                  <button
                    type="button"
                    className="simulation-task-panel__row-id"
                    title={task.id}
                    onClick={() => onTaskSelect?.(task)}
                    style={{ border: 0, padding: 0, background: 'transparent', textAlign: 'left' }}
                  >
                    {task.id}
                  </button>
                  <Tag color={statusColor(task.status)}>
                    {t(`tasks.status.${task.status}`, { defaultValue: task.status })}
                  </Tag>
                </div>
                <Progress
                  percent={task.progress}
                  size="small"
                  status={
                    task.status === 'failed' || task.status === 'timed_out'
                      ? 'exception'
                      : task.status === 'completed'
                        ? 'success'
                        : 'active'
                  }
                  aria-label={`${task.id} ${task.progress}%`}
                />
                <div className="simulation-task-panel__row-meta">
                  <span>
                    {label('attempt', {
                      attempt: task.attempt,
                      max: task.max_attempts,
                    })}
                  </span>
                  {task.phase ? <span>{label('phase', { phase: task.phase })}</span> : null}
                  <span>
                    {label('created', {
                      value: formatTimestamp(task.created_at, locale),
                    })}
                  </span>
                  {task.updated_at ? (
                    <span>
                      {label('updated', {
                        value: formatTimestamp(task.updated_at, locale),
                      })}
                    </span>
                  ) : null}
                </div>
                {task.error_code || task.error_message ? (
                  <div className="simulation-task-panel__error" role="status">
                    <strong>{task.error_code ?? label('failed')}</strong>
                    {task.error_message ? ` · ${task.error_message}` : ''}
                  </div>
                ) : null}
                {task.retry_submission_id ? (
                  <div className="simulation-task-panel__summary">
                    {label('retryLinked', { id: task.retry_submission_id })}
                  </div>
                ) : null}
                <div className="simulation-task-panel__actions" style={{ marginTop: 10 }}>
                  {cancellable ? (
                    <Button
                      danger
                      size="small"
                      icon={<CloseCircleOutlined />}
                      loading={cancellationPending}
                      disabled={!canWrite || cancellationPending}
                      onClick={() => void handleCancel(task.id)}
                    >
                      {label('cancel')}
                    </Button>
                  ) : null}
                  {retryable ? (
                    <Tooltip title={retryHasRequest ? undefined : label('retryUnavailable')}>
                      <Button
                        size="small"
                        icon={<RetweetOutlined />}
                        loading={retryPending}
                        disabled={!canWrite || retryPending || !retryHasRequest}
                        onClick={() => void handleRetry(task.id)}
                      >
                        {label('retry')}
                      </Button>
                    </Tooltip>
                  ) : null}
                </div>
              </li>
            )
          })}
        </ol>
      )}

      {controller.hasNextPage ? (
        <footer className="simulation-task-panel__footer">
          <Button
            size="small"
            loading={controller.loadingMore}
            onClick={() => void controller.loadMore()}
          >
            {label('loadMore')}
          </Button>
        </footer>
      ) : null}
    </section>
  )
}

type SimulationTaskPanelBridgeProps = Omit<SimulationTaskPanelProps, 'controller'>

function SimulationTaskPanelBridge(props: SimulationTaskPanelBridgeProps) {
  const controller = useSimulationTask({
    networkId: props.networkId,
    token: props.token,
    client: props.client,
    enableStream: props.enableStream,
    stream: props.stream,
    streamEndpoint: props.streamEndpoint,
    streamFetchImpl: props.streamFetchImpl,
    pollIntervalMs: props.pollIntervalMs,
    hiddenPollIntervalMs: props.hiddenPollIntervalMs,
    connectedPollIntervalMs: props.connectedPollIntervalMs,
    pageSize: props.pageSize,
    keyFactory: props.keyFactory,
    resolveRetryRequest: props.resolveRetryRequest,
  })
  return <SimulationTaskPanelView {...props} controller={controller} />
}

export function SimulationTaskPanel(props: SimulationTaskPanelProps) {
  const { controller, ...bridgeProps } = props
  if (controller) return <SimulationTaskPanelView {...props} controller={controller} />
  return <SimulationTaskPanelBridge {...bridgeProps} />
}

type TranslationFunction = ReturnType<typeof useTranslation>['t']

function createTaskLabel(t: TranslationFunction) {
  return (key: string, options?: Record<string, unknown>) =>
    t(`tasks.${key}`, { ...options, defaultValue: DEFAULT_LABELS[key] ?? key })
}

const DEFAULT_LABELS: Record<string, string> = {
  title: 'Simulation tasks',
  activeSummary: '{{count}} active task(s)',
  streamConnected: 'SSE live · REST safety polling',
  pollingFallback: 'SSE unavailable · REST polling active',
  filter: 'Status filter',
  submit: 'Submit simulation',
  submitUnavailable: 'A valid simulation request is required',
  staleData: 'Showing the last valid task snapshot',
  loadError: 'Unable to load simulation tasks',
  actionError: 'Task action failed',
  loading: 'Loading task history…',
  empty: 'No tasks match this status filter.',
  history: 'Simulation task history',
  created: 'Created {{value}}',
  updated: 'Updated {{value}}',
  attempt: 'Attempt {{attempt}}/{{max}}',
  phase: 'Phase: {{phase}}',
  failed: 'Failed',
  cancel: 'Cancel',
  retry: 'Submit retry',
  retryUnavailable: 'Original request is unavailable after refresh',
  retryLinked: 'Retry submitted as {{id}}',
  loadMore: 'Load more history',
  idempotencyKey: 'Idempotency-Key',
  'idempotency.idle': 'Idle',
  'idempotency.pending': 'Pending / safe to retry',
  'idempotency.new': 'Created',
  'idempotency.replayed': 'Idempotent replay',
  'idempotency.conflict': 'Idempotency conflict',
}
