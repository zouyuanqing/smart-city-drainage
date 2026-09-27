import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import api from '@/services/api'
import { useForecastStreamSubscription } from './ForecastStreamProvider'
import {
  applyForecastEvent,
  normalizeForecastRiskRanking,
  normalizeForecastSnapshot,
  summariseTwoHourForecast,
  topRiskItems,
} from './ranking'
import type { ForecastRiskRankingResponse, ForecastRiskItem, ForecastSnapshot } from './types'

export interface ForecastDashboardLabels {
  loading: string
  error: string
  notReady: string
  stale: string
  ariaLabel: string
  title: string
  summary: (active: number, predicted: number, observed: number) => string
  highestRisk: (targetId: string, score: number) => string
  emptyWindow: string
  emptyRisk: string
  riskListLabel: string
  riskHeading: (topN: number) => string
  scoreUnit: string
  observed: string
  predicted: string
  minutesAfter: (minutes: number) => string
}

export interface ForecastDashboardProps {
  networkId: string
  token: string
  labels?: Partial<ForecastDashboardLabels>
  fetchSnapshot?: (networkId: string, signal?: AbortSignal) => Promise<ForecastSnapshot>
  fetchRiskRanking?: (
    networkId: string,
    limit: number,
    signal?: AbortSignal
  ) => Promise<ForecastRiskRankingResponse>
  topN?: number
}

const loadLatestForecast = async (
  networkId: string,
  signal?: AbortSignal
): Promise<ForecastSnapshot> => {
  const response = await api.get<ForecastSnapshot>('/forecast/latest', {
    params: { network_id: networkId, limit: 200 },
    signal,
  })
  return normalizeForecastSnapshot(response.data, networkId)
}

const loadRiskRanking = async (
  networkId: string,
  limit: number,
  signal?: AbortSignal
): Promise<ForecastRiskRankingResponse> => {
  const response = await api.get<ForecastRiskRankingResponse>('/forecast/risk-ranking', {
    params: { network_id: networkId, horizon_minutes: 120, limit },
    signal,
  })
  return normalizeForecastRiskRanking(response.data, networkId)
}

export default function ForecastDashboard({
  networkId,
  token,
  labels: labelsProp,
  fetchSnapshot = loadLatestForecast,
  fetchRiskRanking = loadRiskRanking,
  topN = 5,
}: ForecastDashboardProps) {
  const { t } = useTranslation()
  const translate = (key: string, defaultValue: string) => t(`forecast.${key}`, { defaultValue })
  const labels: ForecastDashboardLabels = {
    loading: labelsProp?.loading ?? translate('loading', '正在加载预测…'),
    error: labelsProp?.error ?? translate('error', '预测数据暂时不可用'),
    notReady: labelsProp?.notReady ?? translate('notReady', '预测尚未就绪，等待下一次成功发布。'),
    stale: labelsProp?.stale ?? translate('stale', '预测数据已过期，当前风险榜已禁用。'),
    ariaLabel: labelsProp?.ariaLabel ?? translate('ariaLabel', '未来两小时预测'),
    title: labelsProp?.title ?? translate('title', '未来 2 小时预测'),
    summary:
      labelsProp?.summary ??
      ((active, predicted, observed) =>
        translate(
          'summary',
          '当前有效 {{active}} 项 · 预计 {{predicted}} 项 · 已发生 {{observed}} 项'
        )
          .replace('{{active}}', String(active))
          .replace('{{predicted}}', String(predicted))
          .replace('{{observed}}', String(observed))),
    highestRisk:
      labelsProp?.highestRisk ??
      ((targetId, score) =>
        translate('highestRisk', '最高风险：{{id}}（{{score}}）')
          .replace('{{id}}', targetId)
          .replace('{{score}}', String(score))),
    emptyWindow: labelsProp?.emptyWindow ?? translate('emptyWindow', '未来 2 小时暂无风险预警'),
    emptyRisk: labelsProp?.emptyRisk ?? translate('emptyRisk', '暂无风险预警'),
    riskListLabel: labelsProp?.riskListLabel ?? translate('riskListLabel', '风险榜'),
    riskHeading:
      labelsProp?.riskHeading ??
      ((topN) => translate('riskHeading', 'Top-{{topN}} 风险榜').replace('{{topN}}', String(topN))),
    scoreUnit: labelsProp?.scoreUnit ?? translate('scoreUnit', '分'),
    observed: labelsProp?.observed ?? translate('observed', '已发生'),
    predicted: labelsProp?.predicted ?? translate('predicted', '预计'),
    minutesAfter:
      labelsProp?.minutesAfter ??
      ((minutes) =>
        translate('minutesAfter', '{{minutes}} 分钟后').replace('{{minutes}}', String(minutes))),
  }
  const errorMessage = labels.error
  const [snapshot, setSnapshot] = useState<ForecastSnapshot | null>(null)
  const [riskRanking, setRiskRanking] = useState<ForecastRiskRankingResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const fetchSnapshotRef = useRef(fetchSnapshot)
  const fetchRiskRankingRef = useRef(fetchRiskRanking)
  const requestGenerationRef = useRef(0)
  const requestSerialRef = useRef(0)
  const networkAbortRef = useRef<AbortController | null>(null)
  fetchSnapshotRef.current = fetchSnapshot
  fetchRiskRankingRef.current = fetchRiskRanking

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      const generation = requestGenerationRef.current
      const serial = ++requestSerialRef.current
      const isCurrent = () =>
        generation === requestGenerationRef.current &&
        serial === requestSerialRef.current &&
        !signal?.aborted
      const nextSnapshot = await fetchSnapshotRef.current(networkId, signal)
      if (!isCurrent()) return
      if (nextSnapshot.network_id !== networkId) {
        throw new Error('Forecast response network does not match the active network')
      }
      setSnapshot(nextSnapshot)
      if (nextSnapshot.data_state !== 'ready') {
        setRiskRanking(null)
        return
      }
      const nextRisk = await fetchRiskRankingRef.current(networkId, topN, signal)
      if (!isCurrent()) return
      if (nextRisk.network_id !== networkId) {
        throw new Error('Forecast response network does not match the active network')
      }
      setRiskRanking(nextRisk)
    },
    [networkId, topN]
  )

  useForecastStreamSubscription({
    networkId,
    token,
    onEvent: (event) => {
      if (event.network_id !== networkId) return
      setSnapshot((current) =>
        current
          ? {
              ...current,
              alerts: applyForecastEvent(current.alerts, event, networkId),
            }
          : current
      )
      setRiskRanking((current) => {
        if (!current || current.network_id !== networkId) return current
        const alerts = applyForecastEvent(current.items, event, networkId)
        return { ...current, items: alerts as ForecastRiskItem[] }
      })
    },
    onResyncRequired: () => {
      const generation = requestGenerationRef.current
      void refresh().catch(() => {
        if (generation === requestGenerationRef.current) setError(errorMessage)
      })
    },
  })

  useEffect(() => {
    const generation = requestGenerationRef.current + 1
    requestGenerationRef.current = generation
    requestSerialRef.current += 1
    networkAbortRef.current?.abort()
    const controller = new AbortController()
    networkAbortRef.current = controller
    setSnapshot(null)
    setRiskRanking(null)
    setError(null)
    setLoading(true)
    void refresh(controller.signal)
      .catch(() => {
        if (generation === requestGenerationRef.current && !controller.signal.aborted) {
          setError(errorMessage)
        }
      })
      .finally(() => {
        if (generation === requestGenerationRef.current && !controller.signal.aborted) {
          setLoading(false)
        }
      })
    return () => {
      controller.abort()
      if (requestGenerationRef.current === generation) requestGenerationRef.current += 1
    }
  }, [errorMessage, networkId, refresh, topN])

  if (loading && !snapshot) return <section aria-label={labels.ariaLabel}>{labels.loading}</section>
  if (!snapshot) return <section role="alert">{labels.error}</section>

  if (snapshot.data_state === 'not_ready') {
    return (
      <section role="status" aria-label={labels.ariaLabel}>
        {labels.notReady}
      </section>
    )
  }
  if (snapshot.data_state === 'stale') {
    return (
      <section role="status" aria-label={labels.ariaLabel}>
        {labels.stale}
      </section>
    )
  }

  const summary = summariseTwoHourForecast(snapshot, Date.parse(snapshot.server_time))
  const riskItems: ForecastRiskItem[] = topRiskItems(
    riskRanking?.data_state === 'ready' ? riskRanking.items : [],
    topN
  )

  return (
    <section aria-label={labels.ariaLabel} style={{ display: 'grid', gap: 16 }}>
      {error && <p role="status">{error}</p>}
      <header>
        <h2>{labels.title}</h2>
        <p>{labels.summary(summary.active, summary.predicted, summary.observed)}</p>
      </header>
      {summary.highestRisk ? (
        <p>{labels.highestRisk(summary.highestRisk.target.id, summary.highestRisk.risk_score)}</p>
      ) : (
        <p>{labels.emptyWindow}</p>
      )}
      <h3>{labels.riskHeading(topN)}</h3>
      {riskItems.length === 0 ? (
        <p data-testid="forecast-empty">{labels.emptyRisk}</p>
      ) : (
        <ol aria-label={labels.riskListLabel}>
          {riskItems.map((alert) => (
            <li key={alert.id} data-testid="forecast-risk-item">
              <strong>{alert.target.id}</strong> — {alert.type} — {alert.risk_score.toFixed(1)}{' '}
              {labels.scoreUnit} —{' '}
              <span
                data-phase={alert.phase}
                aria-label={alert.phase === 'observed' ? labels.observed : labels.predicted}
              >
                {alert.phase === 'observed' ? labels.observed : labels.predicted}
              </span>
              <small>
                {' '}
                · {alert.expected_at}
                {alert.phase === 'predicted'
                  ? ` · ${labels.minutesAfter(alert.lead_time_minutes)}`
                  : ''}
              </small>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

export { loadLatestForecast, loadRiskRanking }
