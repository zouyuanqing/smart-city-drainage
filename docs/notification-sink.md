# Internal notification-sink (development / demo only)

> **This service is not a paging system and must never be a production
> destination.** It exists so a fresh `docker compose up` has *a* webhook
> target for `notification-worker`, and so the delivery lane can be exercised
> end to end without an external mail relay or a public HTTPS receiver.
> Production must replace it. See [Production replacement](#production-replacement).

## What it is

`notification-sink` is a small, dependency-free Python HTTP service
(`docker/notification-sink/sink.py`, stdlib only) that runs in the default
Compose stack as an internal-only service.

| Property | Value |
| --- | --- |
| Endpoints | `GET /health`, `GET /alerts`, `POST /alerts` |
| Network | `expose: ["8030"]` on `scn-network`, **no `ports:`** — never published on the host |
| Proxied by Nginx | No |
| Auth | HMAC-SHA256 over the raw body, `X-Forecast-Signature: sha256=<hex>` |
| Image | `smart-city-drainage-backend:local` (same local build as the workers) |
| User | `10001:10001`, `read_only: true`, `cap_drop: ALL`, `no-new-privileges` |
| State | Bounded in-memory ring buffer (200) + one JSON line per delivery on stdout |

It refuses to start without `ALERT_WEBHOOK_SECRET` — the same fail-closed
posture as the worker's `probe_webhook_secret`. An unsigned or mis-signed
`POST /alerts` gets `401` and is **never recorded**, so a delivery in
`GET /alerts` is always a verified signature round-trip rather than a caller
asserting one.

The signature it checks is byte-identical to the one
`app/services/notification_service.py` produces
(`hmac_sha256(secret, raw_body)`, computed over the exact
`json.dumps(..., sort_keys=True, separators=(",", ":"))` bytes that were sent).

## Why it exists

`notification-worker` is the only process that turns
`hydraulic.notification_deliveries` rows into external side effects, and its
`_probe_transport` startup check (`app/workers/notification_worker.py:561`)
**refuses to start** when neither SMTP-with-recipients nor a webhook URL is
configured:

```
no delivery transport configured: enable SMTP with ALERT_EMAIL_RECIPIENTS
or set ALERT_WEBHOOK_URLS
```

On a stock deployment there is no mail relay and no public HTTPS receiver, so
the worker exited `EXIT_STARTUP_UNAVAILABLE`, published no heartbeat, and the
API's `/readyz` failed on `worker_heartbeats.notification`. The delivery lane
was unrunnable and untestable.

The sink gives the default stack a real, reachable, signature-verifying
destination so that lane is runnable again.

## Configuration in the default Compose file

`docker-compose.yml`, `notification-worker`:

```yaml
WEBHOOK_ENABLED: ${WEBHOOK_ENABLED:-true}
ALERT_WEBHOOK_URLS: ${ALERT_WEBHOOK_URLS:-["http://notification-sink:8030/alerts"]}
ALERT_WEBHOOK_SECRET: ${ALERT_WEBHOOK_SECRET:-dev-only-not-a-real-secret-change-me}
```

Two things to know about these:

* **`WEBHOOK_ENABLED` had to be added.** `send_webhook` returns `False`
  immediately when it is off (`notification_service.py:124`), so a webhook URL
  alone would be counted as *attempted and failed*. The worker did not set it
  before.
* **The secret is a dev/demo placeholder, printed in a tracked file.** It is
  not a `Settings` field — `resolve_webhook_secret` reads `os.environ`
  directly. It therefore cannot go in `.env.example` (whose keys must match
  `Settings.model_fields` exactly) nor in the project-root `.env` (which
  `Settings` reads with `extra="forbid"` and would refuse to load). Export it
  from the deployment environment. Note that `.env.example` is additionally
  **SHA-256-frozen** by
  `backend/tests/fixtures/hydraulic/contracts/dependency-handoff-v1.json`;
  even a comment-only edit there fails
  `scripts/check_dependency_handoff.py` with
  `result SHA-256 mismatch: .env.example`. It is deliberately left untouched
  by this change.

## Known limitation: the worker does **not** deliver to this sink

This is the important part, and it is deliberate.

`NotificationService._is_https_webhook` (`notification_service.py:548`) is an
SSRF allowlist. A webhook target is accepted only if it is:

* `https` (plain `http` is rejected outright),
* on port 443 (any other port is rejected),
* **not** `localhost` / `*.localhost`,
* and resolves to **no** private, loopback, link-local, multicast, reserved or
  unspecified address.

`http://notification-sink:8030/alerts` fails on the first, second and fourth
of those. `notification-sink` is a Compose-internal hostname resolving to a
private `172.x` address.

The consequence, precisely: `notify_forecast_event` counts the URL as an
invalid destination, so it reports `webhook_attempted=1, webhook_succeeded=0,
webhook_failed=1`, and the worker settles the delivery row as **`suppressed`**
with `last_error_code = 'WEBHOOK_DELIVERY_FAILED'`. The sink records nothing.

**That is the guard working, not a misconfiguration.** The allowlist is
covered by a deliberate regression test
(`backend/tests/hydraulic/test_forecast_round1_regressions.py:317`,
`test_webhook_dns_rebinding_is_rejected`). Relaxing it to reach an in-network
sink would reopen an SSRF path and would send signed alert payloads in
cleartext — so it has not been relaxed here, and
`test_ssrf_allowlist_refuses_the_internal_sink_url` pins the behaviour.

If you need the worker to actually deliver on a private network, the change
belongs in `notification_service.py` as a narrowly-scoped, operator-pinned
"internal trusted relay" allowance — not in Compose, and not as a blanket
relaxation of the public-HTTPS rule.

## Production replacement

**Before any real deployment, override both variables and remove the sink
service.** The shipped values are not fit for production:

```bash
export ALERT_WEBHOOK_URLS='["https://alerts.example.com/hydraulic"]'
export ALERT_WEBHOOK_SECRET="$(openssl rand -hex 32)"   # or your vault's value
```

Or, for email instead of webhooks:

```bash
export SMTP_ENABLED=true
export SMTP_HOST=smtp.your-provider.com
export SMTP_PORT=587
export SMTP_USE_TLS=true
export SMTP_USERNAME=alerts@your-domain.com
export SMTP_PASSWORD=...
export ALERT_EMAIL_RECIPIENTS='["oncall@your-domain.com"]'
```

Checklist:

- [ ] `ALERT_WEBHOOK_URLS` points at a **public HTTPS endpoint on port 443**,
      or is left empty in favour of SMTP.
- [ ] `ALERT_WEBHOOK_SECRET` is a real secret from your secret store — never
      the `dev-only-not-a-real-secret-change-me` fallback.
- [ ] The `notification-sink` service is removed from the Compose file (or the
      whole default profile is not used). Leaving it in place means a service
      holding your production webhook secret is running in your network.
- [ ] `WEBHOOK_ENABLED` is `true` if — and only if — a webhook URL is set.
- [ ] The receiving endpoint validates the same `X-Forecast-Signature`
      (`sha256=<hmac_sha256(secret, raw_body)>`) and honours the
      `Idempotency-Key` header.

`notification-worker` will refuse to start if `ALERT_WEBHOOK_URLS` is set
without `ALERT_WEBHOOK_SECRET` — that check (`probe_webhook_secret`) is
unconditional and stricter than the API's environment-gated rule. It is
unchanged by this work.

## Operating the sink

```bash
# Health (from inside the compose network, or via the healthcheck)
docker compose exec notification-sink \
  python /opt/scn/healthcheck.py --url http://127.0.0.1:8030/health

# The delivery trail — one JSON line per accepted or refused POST
docker compose logs -f notification-sink

# Recent recorded deliveries, including full payloads
docker compose exec notification-sink python - <<'PY'
import json, urllib.request
print(urllib.request.urlopen("http://127.0.0.1:8030/alerts").read().decode())
PY
```

Accepted deliveries look like:

```json
{"log": "alert_received", "event_id": "…", "event_type": "alert.created", "bytes": 412, "accepted_total": 1}
```

Refused ones are explicit and are never counted as deliveries:

```json
{"log": "alert_rejected", "reason": "signature_invalid", "event_id": "…", "bytes": 412}
```

## Tests

```bash
python -m pytest docker/notification-sink/tests/test_notification_sink.py -q
python -m pytest docker/backend/tests/test_deployment_contract.py -q
```
