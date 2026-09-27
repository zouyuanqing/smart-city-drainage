import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { SimulationTaskPanel } from '../tasks/TaskPanel'
import { SimulationTaskClientError } from '../tasks/api'
import type { SimulationSubmitRequest, SimulationTask, SimulationTaskClient } from '../tasks/types'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'en-US' },
    t: (key: string, options?: { defaultValue?: string; [key: string]: unknown }) => {
      let value = options?.defaultValue ?? key
      for (const [name, replacement] of Object.entries(options ?? {})) {
        if (name === 'defaultValue' || typeof replacement === 'undefined') continue
        value = value.split(`{{${name}}}`).join(String(replacement))
      }
      return value
    },
  }),
}))

const request: SimulationSubmitRequest = {
  network_id: 'network-1',
  network_revision_id: 'revision-1',
  rainfall_scenario_id: 'rainfall-1',
  parameter_set_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  solver_selection: 'auto',
  start_at: '2026-09-24T00:00:00Z',
  duration_seconds: 86_400,
  frame_interval_seconds: 300,
  ponding_enabled: false,
  strict_stability: true,
}

function task(overrides: Partial<SimulationTask> = {}): SimulationTask {
  return {
    id: 'simulation-1',
    status: 'running',
    progress: 50,
    attempt: 1,
    max_attempts: 3,
    network_id: 'network-1',
    network_revision_id: 'revision-1',
    target_manifest_id: '33333333-3333-4333-8333-333333333333',
    target_manifest_sha256: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    rainfall_scenario_id: 'rainfall-1',
    solver_selection: 'auto',
    solver_id: 'py_mw',
    solver_version: 'fixture-1',
    created_at: '2026-09-24T00:00:00Z',
    updated_at: '2026-09-24T00:01:00Z',
    ...overrides,
  } as SimulationTask
}

function createClient(overrides: Partial<SimulationTaskClient> = {}): SimulationTaskClient {
  return {
    list: vi.fn(async () => ({ items: [], nextCursor: null })),
    get: vi.fn(async () => task()),
    submit: vi.fn(async () => ({ task: task(), idempotencyReplayed: false })),
    cancel: vi.fn(async () => ({ task: task(), idempotencyReplayed: false })),
    ...overrides,
  }
}

describe('SimulationTaskPanel', () => {
  it('renders progress/history, filters status, and exposes submit retry/idempotency state', async () => {
    const running = task({ progress: 92, updated_at: '2026-09-24T00:02:00Z' })
    const failed = task({
      id: 'simulation-2',
      status: 'failed',
      progress: 73,
      attempt: 3,
      error_code: 'RESULT_PERSIST_FAILED',
      error_message: 'result transaction failed',
      created_at: '2026-09-23T23:00:00Z',
      updated_at: '2026-09-23T23:30:00Z',
      request,
    })
    const replacement = task({
      id: 'simulation-3',
      status: 'queued',
      progress: 0,
      attempt: 0,
      created_at: '2026-09-24T00:04:00Z',
      updated_at: '2026-09-24T00:04:00Z',
    })
    const client = createClient({
      list: vi.fn(async () => ({ items: [running, failed], nextCursor: null })),
      submit: vi.fn(async () => ({ task: replacement, idempotencyReplayed: false })),
    })
    let key = 0

    render(
      <SimulationTaskPanel
        networkId="network-1"
        client={client}
        canWrite
        submitRequest={request}
        enableStream={false}
        pollIntervalMs={60_000}
        keyFactory={() => `panel-key-${++key}`}
      />
    )

    await waitFor(() => expect(screen.getAllByTestId('simulation-task-row')).toHaveLength(2))
    expect(screen.getByText('92%')).toBeInTheDocument()
    expect(screen.getByText('RESULT_PERSIST_FAILED')).toBeInTheDocument()
    expect(screen.getByText(/result transaction failed/)).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Status filter'), { target: { value: 'failed' } })
    await waitFor(() => expect(screen.getAllByTestId('simulation-task-row')).toHaveLength(1))
    expect(screen.getByTestId('simulation-task-row')).toHaveAttribute('data-status', 'failed')

    fireEvent.click(screen.getByRole('button', { name: /Submit simulation/ }))
    await waitFor(() =>
      expect(screen.getByTestId('idempotency-receipt')).toHaveAttribute('data-state', 'new')
    )
    expect(screen.getByTestId('idempotency-receipt')).toHaveTextContent('panel-key-1')

    fireEvent.click(screen.getByRole('button', { name: /Submit retry/ }))
    await waitFor(() =>
      expect(screen.getByText('Retry submitted as simulation-3')).toBeInTheDocument()
    )
    expect(client.submit).toHaveBeenCalledTimes(2)
    expect((client.submit as ReturnType<typeof vi.fn>).mock.calls[0]?.[1]).toBe('panel-key-1')
    expect((client.submit as ReturnType<typeof vi.fn>).mock.calls[1]?.[1]).toBe('panel-key-2')

    const failedRow = screen.getByTestId('simulation-task-row')
    expect(within(failedRow).getByText('Retry submitted as simulation-3')).toBeInTheDocument()
  })

  it('keeps the last snapshot visible and shows a structured REST load error', async () => {
    const client = createClient({
      list: vi.fn(async () => {
        throw new SimulationTaskClientError({
          status: 503,
          code: 'SOLVER_UNAVAILABLE',
          detail: 'simulation control plane is unavailable',
          errors: [],
          requestId: 'request-123',
        })
      }),
    })

    render(
      <SimulationTaskPanel
        networkId="network-1"
        client={client}
        enableStream={false}
        pollIntervalMs={60_000}
      />
    )

    await waitFor(() =>
      expect(screen.getByText('simulation control plane is unavailable')).toBeInTheDocument()
    )
    expect(screen.getByText('Unable to load simulation tasks')).toBeInTheDocument()
  })
})
