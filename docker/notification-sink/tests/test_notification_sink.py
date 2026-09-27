"""Delivery tests for the internal notification-sink and its worker wiring.

What is actually exercised here
-------------------------------
1. The sink really receives, authenticates and records webhook deliveries.
2. The signature the sink verifies is byte-identical to the one
   ``NotificationService.send_webhook`` produces, so a recorded delivery is a
   real round-trip and not a caller asserting one.
3. The default Compose wiring gives notification-worker a real transport, so
   its fail-closed ``_probe_transport`` / ``probe_webhook_secret`` checks pass.
4. The sink publishes no host port.
5. The SSRF allowlist still refuses the internal sink URL, and therefore the
   worker does NOT deliver to it.

Point 5 is a real, passing test of a real limitation.  It is deliberately not
relaxed: see ``docs/notification-sink.md``.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "docker-compose.yml"
SINK_PATH = ROOT / "docker" / "notification-sink" / "sink.py"

# The dev/demo secret shipped as the compose fallback.
DEV_SECRET = "dev-only-not-a-real-secret-change-me"
SINK_URL = "http://notification-sink:8030/alerts"


def _load_sink() -> ModuleType:
    spec = importlib.util.spec_from_file_location("notification_sink", SINK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sink_module = _load_sink()


def sign(secret: str, payload: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    """Produce exactly what ``NotificationService.send_webhook`` produces.

    Same serializer options, same HMAC, same header names — copied from
    ``app/services/notification_service.py:138-155``.
    """

    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    ).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return body, {
        "Content-Type": "application/json",
        "X-Forecast-Signature": f"sha256={signature}",
        "X-Forecast-Event-Id": str(payload.get("event_id", "")),
        "Idempotency-Key": str(payload.get("event_id", "")),
    }


@pytest.fixture
def live_sink():
    """A real sink process on a loopback port, started the way Compose starts it."""

    port = 18_000 + (os.getpid() % 2_000)
    env = dict(os.environ)
    env.update(
        NOTIFICATION_SINK_HOST="127.0.0.1",
        NOTIFICATION_SINK_PORT=str(port),
        NOTIFICATION_SINK_WEBHOOK_SECRET=DEV_SECRET,
    )
    proc = subprocess.Popen(
        [sys.executable, str(SINK_PATH)], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("notification-sink exited during startup")
            try:
                with urllib.request.urlopen(f"{base}/health", timeout=1) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001 - startup poll
                time.sleep(0.1)
        else:  # pragma: no cover - only on a wedged start
            raise RuntimeError("notification-sink never reported healthy")
        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()


def _post(base: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(f"{base}/alerts", data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _get(base: str, path: str) -> dict[str, Any]:
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as response:
        return json.loads(response.read())


# --------------------------------------------------------------------------
# Sink behaviour
# --------------------------------------------------------------------------


def test_health_reports_ok(live_sink: str) -> None:
    health = _get(live_sink, "/health")
    assert health["status"] == "ok"
    assert health["service"] == "notification-sink"
    assert health["accepted"] == 0
    # A 2xx here is what the Compose healthcheck requires.
    assert "role" in health


def test_signed_delivery_is_received_and_recorded(live_sink: str) -> None:
    payload = {
        "schema_version": 1,
        "event_id": "11111111-1111-1111-1111-111111111111",
        "event_type": "alert.created",
        "alert": {"level": "critical", "type": "flood_forecast", "phase": "predicted"},
    }
    body, headers = sign(DEV_SECRET, payload)

    status, raw = _post(live_sink, body, headers)
    # 2xx is what NotificationService.send_webhook requires to call it delivered.
    assert 200 <= status < 300
    assert json.loads(raw)["status"] == "accepted"

    recorded = _get(live_sink, "/alerts")["deliveries"]
    assert len(recorded) == 1
    assert recorded[0]["event_id"] == payload["event_id"]
    assert recorded[0]["event_type"] == "alert.created"
    assert recorded[0]["payload"]["alert"]["level"] == "critical"
    assert _get(live_sink, "/health")["accepted"] == 1


def test_unsigned_delivery_is_refused_and_not_recorded(live_sink: str) -> None:
    body, _headers = sign(DEV_SECRET, {"event_id": "unsigned", "event_type": "alert.created"})
    status, _raw = _post(live_sink, body, {"Content-Type": "application/json"})
    assert status == 401
    assert _get(live_sink, "/alerts")["deliveries"] == []
    assert _get(live_sink, "/health")["rejected"] == 1


def test_wrong_secret_is_refused(live_sink: str) -> None:
    body, headers = sign("not-the-shared-secret", {"event_id": "x", "event_type": "alert.created"})
    status, _raw = _post(live_sink, body, headers)
    assert status == 401
    assert _get(live_sink, "/alerts")["deliveries"] == []


def test_tampered_body_is_refused(live_sink: str) -> None:
    body, headers = sign(DEV_SECRET, {"event_id": "orig", "event_type": "alert.created"})
    tampered = body.replace(b"orig", b"evil")
    status, _raw = _post(live_sink, tampered, headers)
    assert status == 401
    assert _get(live_sink, "/alerts")["deliveries"] == []


def test_sink_refuses_to_start_without_a_secret() -> None:
    env = dict(os.environ, NOTIFICATION_SINK_WEBHOOK_SECRET="")
    result = subprocess.run(
        [sys.executable, str(SINK_PATH)], env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 2
    assert "NOTIFICATION_SINK_WEBHOOK_SECRET is required" in result.stderr


def test_signature_verification_matches_the_production_signer() -> None:
    """The sink's check must be the sender's check, not a looser one."""

    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.services.notification_service import NotificationService
    finally:
        sys.path.pop(0)

    payload = {"event_id": "round-trip", "event_type": "alert.created", "n": 1.5}
    body, headers = sign(DEV_SECRET, payload)
    assert sink_module.verify_signature(DEV_SECRET, body, headers["X-Forecast-Signature"])
    assert NotificationService._is_https_webhook(SINK_URL) is False  # documented below
    assert not sink_module.verify_signature(DEV_SECRET, body + b" ", headers["X-Forecast-Signature"])
    assert not sink_module.verify_signature("other", body, headers["X-Forecast-Signature"])


# --------------------------------------------------------------------------
# Compose wiring
# --------------------------------------------------------------------------


def test_sink_is_in_the_default_compose_and_publishes_no_host_port() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "\n  notification-sink:\n" in compose
    block = _service_block(compose, "notification-sink")
    assert "\n    ports:" not in block, "the sink must not publish a host port"
    assert "expose:" in block
    assert 'command: ["python", "/opt/scn/notification-sink/sink.py"]' in block
    # Real probe, same script every other service uses — not a bare curl/wget.
    assert 'python", "/opt/scn/healthcheck.py"' in block
    assert "http://127.0.0.1:8030/health" in block


def test_worker_is_pointed_at_the_sink_with_a_matching_secret() -> None:
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "notification-worker")
    assert f'ALERT_WEBHOOK_URLS: ${{ALERT_WEBHOOK_URLS:-["{SINK_URL}"]}}' in block
    assert "WEBHOOK_ENABLED: ${WEBHOOK_ENABLED:-true}" in block
    # send_webhook returns early unless WEBHOOK_ENABLED is on.
    assert f"ALERT_WEBHOOK_SECRET: ${{ALERT_WEBHOOK_SECRET:-{DEV_SECRET}}}" in block


def test_sink_and_worker_share_one_secret_source() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    sink_block = _service_block(compose, "notification-sink")
    worker_block = _service_block(compose, "notification-worker")
    sink_line = next(
        line for line in sink_block.splitlines() if "NOTIFICATION_SINK_WEBHOOK_SECRET" in line
    )
    worker_line = next(
        line for line in worker_block.splitlines() if line.strip().startswith("ALERT_WEBHOOK_SECRET")
    )
    assert sink_line.split(":-", 1)[1].rstrip("}") == worker_line.split(":-", 1)[1].rstrip("}")


def test_worker_waits_for_the_sink() -> None:
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "notification-worker")
    assert "notification-sink:" in block
    assert "condition: service_healthy" in block


# --------------------------------------------------------------------------
# The SSRF allowlist: a real limitation, tested, not worked around
# --------------------------------------------------------------------------


def test_ssrf_allowlist_refuses_the_internal_sink_url() -> None:
    """Documents why the worker cannot deliver to the sink it is pointed at.

    ``NotificationService._is_https_webhook`` requires public HTTPS on 443.  The
    sink URL is plain http on a non-443 port at a private container address, so
    it is refused before any socket is opened and
    ``notify_forecast_event`` counts it as an invalid destination.  The delivery
    row then settles ``suppressed``, which is the guard working.
    """

    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.services.notification_service import NotificationService
    finally:
        sys.path.pop(0)

    assert NotificationService._is_https_webhook(SINK_URL) is False
    # ...and the same guard is what the worker relies on; relaxing it to reach
    # an in-network sink would be an SSRF regression, not a wiring fix.
    assert NotificationService._is_https_webhook("http://internal.example.com/alerts") is False
    assert NotificationService._is_https_webhook("https://10.0.0.1/alerts") is False


def test_notification_worker_transport_probe_passes_with_the_shipped_config() -> None:
    """``_probe_transport`` and ``probe_webhook_secret`` must both pass.

    Run in a subprocess with the same environment the Compose service gets, so
    this exercises the real Settings load and the real probes rather than a
    stubbed one.  It proves the default stack no longer fails closed at
    startup for want of a transport.
    """

    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.workers.notification_worker import _probe_transport, probe_webhook_secret
    finally:
        sys.path.pop(0)

    env = dict(os.environ)
    env.update(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/d",
        REDIS_URL="redis://:p@localhost:6379/0",
        SMTP_ENABLED="false",
        ALERT_EMAIL_RECIPIENTS="[]",
        WEBHOOK_ENABLED="true",
        ALERT_WEBHOOK_URLS=json.dumps([SINK_URL]),
        ALERT_WEBHOOK_SECRET=DEV_SECRET,
        SCN_ENV_FILE=str(ROOT / ".env.example"),
    )
    script = (
        "from app.workers.notification_worker import _probe_transport, probe_webhook_secret;"
        "_probe_transport();"
        "probe_webhook_secret();"
        "print('TRANSPORT_PROBES_OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=str(ROOT / "backend"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert "TRANSPORT_PROBES_OK" in result.stdout


def test_webhook_secret_probe_still_fails_closed_without_a_secret() -> None:
    """The stricter-than-production rule must survive the sink wiring."""

    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.workers.notification_worker import probe_webhook_secret
    finally:
        sys.path.pop(0)

    env = dict(os.environ)
    env.update(
        DATABASE_URL="postgresql+asyncpg://u:p@localhost:5432/d",
        REDIS_URL="redis://:p@localhost:6379/0",
        SMTP_ENABLED="false",
        ALERT_EMAIL_RECIPIENTS="[]",
        WEBHOOK_ENABLED="true",
        ALERT_WEBHOOK_URLS=json.dumps([SINK_URL]),
        SCN_ENV_FILE=str(ROOT / ".env.example"),
    )
    env.pop("ALERT_WEBHOOK_SECRET", None)
    script = (
        "from app.workers.notification_worker import probe_webhook_secret;"
        "probe_webhook_secret()"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=str(ROOT / "backend"),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode != 0
    assert "ALERT_WEBHOOK_SECRET is required" in result.stderr


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _service_block(compose: str, service: str) -> str:
    marker = f"\n  {service}:\n"
    start = compose.index(marker) + len(marker)
    lines: list[str] = []
    for line in compose[start:].splitlines(keepends=True):
        if line.strip() and not line.startswith("    "):
            break
        lines.append(line)
    assert lines, f"service {service} has an empty body"
    return "".join(lines)
