#!/usr/bin/env python3
"""Offline validation for the hydraulic monitoring contract and dashboard."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMETHEUS_DIR = ROOT / "docker" / "prometheus"
GRAFANA_DIR = ROOT / "docker" / "grafana"

CONTRACT_METRICS = (
    "simulation_queue_depth",
    "simulation_oldest_queued_seconds",
    "simulation_duration_seconds",
    "simulation_failures_total",
    "simulation_retries_total",
    "simulation_cancelled_total",
    "simulation_timeout_total",
    "simulation_active_slots",
    "solver_fallback_total",
    "simulation_frames_total",
    "forecast_nowcast_duration_seconds",
    "forecast_nowcast_to_alert_seconds",
    "forecast_runs_total",
    "forecast_alerts_created_total",
    "forecast_alert_candidates_suppressed_total",
    "forecast_active_alerts",
    "hydraulic_event_publish_lag_seconds",
    "hydraulic_event_outbox_backlog",
    "forecast_sse_queue_overflow_total",
    "notification_delivery_total",
    "hydraulic_cleanup_pending_total",
)
FORBIDDEN_LABEL_PREFIXES = ("simulation_id=", "alert_id=", "event_id=")
DASHBOARD_RECORDING_ALIASES = {
    "hydraulic_event_publish_lag_seconds": "hydraulic:event_publish_lag_seconds",
    "hydraulic_event_outbox_backlog": "hydraulic:forecast_outbox_backlog",
}


def fail(message: str) -> None:
    raise AssertionError(message)


def main() -> int:
    prometheus_files = sorted(PROMETHEUS_DIR.rglob("*.yml"))
    dashboard_path = GRAFANA_DIR / "dashboards" / "hydraulic-overview.json"
    dashboard = json.loads(dashboard_path.read_text(encoding="utf-8"))
    monitoring_text = "\n".join(
        [path.read_text(encoding="utf-8") for path in prometheus_files]
        + [dashboard_path.read_text(encoding="utf-8")]
    )
    missing = [metric for metric in CONTRACT_METRICS if metric not in monitoring_text]
    if missing:
        fail(f"monitoring configuration is missing contract metrics: {', '.join(missing)}")

    if dashboard.get("uid") != "smart-city-hydraulic":
        fail("Grafana dashboard UID is not stable")
    panels = dashboard.get("panels", [])
    if not panels:
        fail("Grafana dashboard contains no panels")
    expressions = [
        target.get("expr", "")
        for panel in panels
        for target in panel.get("targets", [])
    ]
    missing_dashboard = [
        metric
        for metric in CONTRACT_METRICS
        if not any(
            metric in expr or DASHBOARD_RECORDING_ALIASES.get(metric, metric) in expr
            for expr in expressions
        )
    ]
    if missing_dashboard:
        fail(f"Grafana dashboard is missing contract metrics: {', '.join(missing_dashboard)}")

    combined_promql = "\n".join(expressions) + "\n" + monitoring_text
    forbidden = [label for label in FORBIDDEN_LABEL_PREFIXES if label in combined_promql]
    if forbidden:
        fail(f"high-cardinality labels found in PromQL: {', '.join(forbidden)}")

    datasource = (GRAFANA_DIR / "provisioning" / "datasources" / "prometheus.yml").read_text(
        encoding="utf-8"
    )
    provider = (GRAFANA_DIR / "provisioning" / "dashboards" / "hydraulic.yml").read_text(
        encoding="utf-8"
    )
    if "uid: prometheus" not in datasource or "http://prometheus:9090" not in datasource:
        fail("Grafana Prometheus datasource provisioning is incomplete")
    if "path: /var/lib/grafana/dashboards" not in provider:
        fail("Grafana dashboard provider path is incorrect")

    print("MONITORING_VALIDATION=PASS")
    print(f"CONTRACT_METRICS={len(CONTRACT_METRICS)}")
    print(f"GRAFANA_PANELS={len(panels)}")
    print(f"PROMQL_TARGETS={len(expressions)}")
    print("HIGH_CARDINALITY_ID_LABELS=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, OSError, json.JSONDecodeError) as exc:
        print(f"MONITORING_VALIDATION=FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
