#!/usr/bin/env python3
"""Internal notification-sink: a real, receiving webhook endpoint for the
default Compose stack.

Why this exists
---------------
``notification-worker`` is the only process that turns
``hydraulic.notification_deliveries`` rows into external side effects, and its
``_probe_transport`` startup check refuses to run with no destination
configured.  On a fresh ``docker compose up`` there is no reachable mail relay
and no public HTTPS webhook receiver, so the worker would fail closed at
startup and the whole delivery lane would be untestable.

This service is that receiver.  It is deliberately:

* **internal only** — Compose gives it ``expose:`` and no ``ports:``, so it is
  reachable from the ``scn-network`` bridge and from nothing else.  It is not
  published on the host and is not proxied by Nginx.
* **authenticating** — every ``POST /alerts`` must carry the same
  ``X-Forecast-Signature: sha256=<hmac>`` header that
  ``app.services.notification_service.NotificationService.send_webhook``
  computes (``hmac_sha256(secret, raw_body)``).  An unsigned or mis-signed body
  is refused with 401 and is never recorded.  The sink therefore proves a real
  signature round-trip; it does not accept anything a caller merely asserts.
* **recording** — accepted deliveries are kept in a bounded in-memory ring
  buffer, echoed by ``GET /alerts``, and written to stdout as one JSON line
  each so ``docker compose logs notification-sink`` is a durable, greppable
  record.  Nothing is written to disk: the container runs read-only, and a
  named volume would have to be created in the image to avoid the
  root-owned-volume trap the backend Dockerfile calls out.
* **a development/demo aid** — see ``docs/notification-sink.md``.  It is not a
  paging system and it must never be the production destination.

Scope warning, stated up front
------------------------------
This sink does **not** make the notification worker deliver to it.  The
worker refuses to send a webhook to any URL that is not public HTTPS on port
443 (``NotificationService._is_https_webhook``), and an internal Compose
hostname is neither.  See ``docs/notification-sink.md`` for the full
explanation and the production replacement.

Environment
-----------
``NOTIFICATION_SINK_HOST``        bind address (default ``0.0.0.0``)
``NOTIFICATION_SINK_PORT``        bind port    (default ``8030``)
``NOTIFICATION_SINK_WEBHOOK_SECRET``  shared HMAC secret; **required**
``NOTIFICATION_SINK_MAX_BODY_BYTES``  body cap  (default ``1048576``)
``NOTIFICATION_SINK_HISTORY``     ring-buffer depth (default ``200``)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

SIGNATURE_HEADER = "X-Forecast-Signature"
EVENT_ID_HEADER = "X-Forecast-Event-Id"
IDEMPOTENCY_HEADER = "Idempotency-Key"
SIGNATURE_PREFIX = "sha256="

DEFAULT_PORT = 8030
DEFAULT_MAX_BODY_BYTES = 1_048_576
DEFAULT_HISTORY = 200


class SinkConfigError(RuntimeError):
    """The sink is misconfigured and must not start."""


class SinkConfig:
    """Validated runtime configuration."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        secret: str,
        max_body_bytes: int,
        history: int,
    ) -> None:
        if not secret:
            # Fail closed exactly like the worker: an endpoint that accepts
            # unsigned traffic would make the signature round-trip meaningless.
            raise SinkConfigError(
                "NOTIFICATION_SINK_WEBHOOK_SECRET is required; refusing to "
                "record webhook deliveries that carry no verifiable signature"
            )
        if not 0 < port < 65536:
            raise SinkConfigError("NOTIFICATION_SINK_PORT must be a valid TCP port")
        if max_body_bytes <= 0:
            raise SinkConfigError("NOTIFICATION_SINK_MAX_BODY_BYTES must be positive")
        if history <= 0:
            raise SinkConfigError("NOTIFICATION_SINK_HISTORY must be positive")
        self.host = host
        self.port = port
        self.secret = secret
        self.max_body_bytes = max_body_bytes
        self.history = history


def load_config(environ: dict[str, str] | None = None) -> SinkConfig:
    env = os.environ if environ is None else environ
    return SinkConfig(
        host=str(env.get("NOTIFICATION_SINK_HOST", "0.0.0.0")).strip() or "0.0.0.0",
        port=int(str(env.get("NOTIFICATION_SINK_PORT", DEFAULT_PORT)).strip()),
        secret=str(env.get("NOTIFICATION_SINK_WEBHOOK_SECRET", "")).strip(),
        max_body_bytes=int(
            str(env.get("NOTIFICATION_SINK_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES)).strip()
        ),
        history=int(str(env.get("NOTIFICATION_SINK_HISTORY", DEFAULT_HISTORY)).strip()),
    )


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Constant-time check of ``sha256=<hmac_sha256(secret, body)>``.

    Mirrors the signer in ``NotificationService.send_webhook`` byte for byte:
    the HMAC covers the raw request body, not a re-serialised object.
    """

    if not header:
        return False
    if not header.startswith(SIGNATURE_PREFIX):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header[len(SIGNATURE_PREFIX) :].strip())


class DeliveryLog:
    """Thread-safe bounded record of accepted deliveries."""

    def __init__(self, depth: int) -> None:
        self._entries: deque[dict[str, Any]] = deque(maxlen=depth)
        self._lock = threading.Lock()
        self.accepted = 0
        self.rejected = 0
        self.started_at = time.time()

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self.accepted += 1
            self._entries.append(entry)

    def reject(self) -> None:
        with self._lock:
            self.rejected += 1

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "accepted": self.accepted,
                "rejected": self.rejected,
                "retained": len(self._entries),
                "started_at": self.started_at,
                "uptime_seconds": round(time.time() - self.started_at, 3),
            }


def _log(line: str) -> None:
    """Structured stdout record; the operator-visible delivery trail."""

    print(line, file=sys.stdout, flush=True)


class NotificationSinkHandler(BaseHTTPRequestHandler):
    """``GET /health``, ``GET /alerts``, ``POST /alerts``."""

    server_version = "notification-sink/1.0"
    protocol_version = "HTTP/1.1"

    # Injected by make_server().
    config: SinkConfig
    deliveries: DeliveryLog

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/health":
            stats = self.deliveries.stats()
            self._respond(
                200,
                {
                    "status": "ok",
                    "service": "notification-sink",
                    "role": "internal development/demo webhook receiver",
                    **stats,
                },
            )
            return
        if self.path == "/alerts":
            self._respond(200, {"deliveries": self.deliveries.snapshot()})
            return
        self._respond(404, {"error": "not_found", "path": self.path})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path != "/alerts":
            self._respond(404, {"error": "not_found", "path": self.path})
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._respond(400, {"error": "bad_content_length"})
            return
        if length <= 0:
            self._respond(400, {"error": "empty_body"})
            return
        if length > self.config.max_body_bytes:
            self._respond(
                413,
                {"error": "payload_too_large", "limit": self.config.max_body_bytes},
            )
            return

        body = self.rfile.read(length)
        if not verify_signature(
            self.config.secret, body, self.headers.get(SIGNATURE_HEADER)
        ):
            # Never recorded: an unverifiable delivery is not evidence of one.
            self.deliveries.reject()
            _log(
                json.dumps(
                    {
                        "log": "alert_rejected",
                        "reason": "signature_invalid",
                        "event_id": self.headers.get(EVENT_ID_HEADER),
                        "bytes": len(body),
                    },
                    sort_keys=True,
                )
            )
            self._respond(401, {"error": "signature_invalid"})
            return

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.deliveries.reject()
            self._respond(400, {"error": "body_not_json"})
            return
        if not isinstance(payload, dict):
            self.deliveries.reject()
            self._respond(400, {"error": "body_not_an_object"})
            return

        event_id = str(
            self.headers.get(EVENT_ID_HEADER) or payload.get("event_id") or ""
        )
        entry = {
            "received_at": time.time(),
            "event_id": event_id,
            "idempotency_key": self.headers.get(IDEMPOTENCY_HEADER),
            "event_type": str(payload.get("event_type", "")),
            "bytes": len(body),
            "payload": payload,
        }
        self.deliveries.record(entry)
        _log(
            json.dumps(
                {
                    "log": "alert_received",
                    "event_id": event_id,
                    "event_type": entry["event_type"],
                    "bytes": len(body),
                    "accepted_total": self.deliveries.accepted,
                },
                sort_keys=True,
            )
        )
        # 2xx: the worker treats any 2xx as delivered
        # (NotificationService.send_webhook).
        self._respond(
            202,
            {
                "status": "accepted",
                "event_id": event_id,
                "accepted_total": self.deliveries.accepted,
            },
        )

    def log_message(self, fmt: str, *args: object) -> None:
        # Route the access log into the same structured stream.
        _log(json.dumps({"log": "http", "detail": fmt % args}, sort_keys=True))


def make_server(config: SinkConfig) -> ThreadingHTTPServer:
    log = DeliveryLog(config.history)
    handler = type(
        "BoundNotificationSinkHandler",
        (NotificationSinkHandler,),
        {"config": config, "deliveries": log},
    )
    server = ThreadingHTTPServer((config.host, config.port), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        config = load_config()
    except (SinkConfigError, ValueError) as exc:
        print(f"notification-sink configuration invalid: {exc}", file=sys.stderr)
        return 2

    server = make_server(config)

    def _shutdown(_signum: int, _frame: object) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    _log(
        json.dumps(
            {
                "log": "listening",
                "service": "notification-sink",
                "host": config.host,
                "port": config.port,
                "signature_header": SIGNATURE_HEADER,
                "note": "internal development/demo receiver; not a production pager",
            },
            sort_keys=True,
        )
    )
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        _log(json.dumps({"log": "stopped", **server.RequestHandlerClass.deliveries.stats()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
