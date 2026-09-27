"""Runtime tests for the durable worker services and their Redis heartbeats.

These exercise the process boundary the four Compose services depend on: the
fail-closed preflight, the readiness evidence a worker must accumulate before
``/readyz`` may answer 200, and the Redis registration key the API's own
``/readyz`` reads.

No test here reaches PostgreSQL, Redis, SMTP, or a webhook endpoint.  The
contract being tested is the *process* contract, and every collaborator is
injected.  A test that needed a live Redis would be a worse test: it would
pass or fail for reasons unrelated to the code under test.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import _WORKER_HEARTBEAT_KEYS  # noqa: E402
from app.workers import durable_runtime as runtime  # noqa: E402
from app.workers import (  # noqa: E402
    network_job_worker,
    notification_worker,
    nowcast_worker,
    outbox_relay,
)


def run(coro):
    return asyncio.run(coro)


class _FakeRedis:
    """A minimal stand-in for the shared Redis client."""

    def __init__(self, *, connected: bool = True, fail: bool = False) -> None:
        self.is_connected = connected
        self._fail = fail
        self.store: dict[str, str] = {}
        self.ttls: list[int] = []
        self.published = 0
        self.deleted: list[str] = []

    async def connect(self) -> None:
        if self._fail:
            raise ConnectionRefusedError("redis unavailable")
        self.is_connected = True

    async def set(self, key: str, value: str, ttl: int = 300) -> None:
        if self._fail:
            raise ConnectionRefusedError("redis unavailable")
        self.store[key] = value
        self.ttls.append(ttl)
        self.published += 1

    async def delete(self, key: str) -> None:
        if self._fail:
            raise ConnectionRefusedError("redis unavailable")
        self.deleted.append(key)
        self.store.pop(key, None)


def patch_redis(fake: _FakeRedis):
    module = mock.MagicMock()
    module.redis_client = fake
    return mock.patch.dict(sys.modules, {"app.core.redis_client": module})


class HeartbeatKeyContractTests(unittest.TestCase):
    """The five keys the API reads must be the five keys the workers write."""

    def test_every_worker_key_is_one_the_api_already_reads(self) -> None:
        pairs = {
            "network-job": network_job_worker.HEARTBEAT_KEY,
            "nowcast": nowcast_worker.HEARTBEAT_KEY,
            "outbox-relay": outbox_relay.HEARTBEAT_KEY,
            "notification": notification_worker.HEARTBEAT_KEY,
        }
        for component, key in pairs.items():
            self.assertEqual(key, _WORKER_HEARTBEAT_KEYS[component])
            self.assertEqual(key, runtime.heartbeat_key_for(component))

    def test_simulation_key_is_unchanged(self) -> None:
        from app.workers.simulation_worker import WORKER_HEARTBEAT_KEY

        self.assertEqual(WORKER_HEARTBEAT_KEY, _WORKER_HEARTBEAT_KEYS["simulation"])

    def test_a_worker_cannot_claim_another_components_key(self) -> None:
        # The key is derived from the component, so a typo or a copy-paste
        # cannot make one service vouch for another's liveness.
        with self.assertRaises(ValueError):
            runtime.heartbeat_key_for("")
        with self.assertRaises(ValueError):
            runtime.heartbeat_key_for("now cast")

    def test_ttl_stays_inside_the_api_freshness_window(self) -> None:
        # The API accepts a key while 0 < now - observed <= 75s.  A TTL at or
        # above that window would let a paused worker keep the API ready.
        self.assertLess(runtime.HEARTBEAT_TTL_SECONDS, 75)


class HeartbeatPublisherTests(unittest.TestCase):
    def test_publish_writes_the_worker_own_key_and_nothing_else(self) -> None:
        fake = _FakeRedis()
        publisher = runtime.RedisHeartbeatPublisher("outbox-relay", now=lambda: 1234.5)
        with patch_redis(fake):
            self.assertTrue(run(publisher.publish_once()))
        self.assertEqual(list(fake.store), ["hydraulic:worker:heartbeat:outbox-relay"])
        self.assertEqual(fake.ttls, [runtime.HEARTBEAT_TTL_SECONDS])
        self.assertEqual(publisher.published, 1)

    def test_publish_reports_failure_instead_of_raising(self) -> None:
        fake = _FakeRedis(connected=False, fail=True)
        publisher = runtime.RedisHeartbeatPublisher("nowcast")
        with patch_redis(fake):
            self.assertFalse(run(publisher.publish_once()))
        self.assertEqual(publisher.published, 0)
        self.assertEqual(publisher.last_error, "ConnectionRefusedError")
        self.assertEqual(fake.store, {})

    def test_close_deletes_only_this_workers_key(self) -> None:
        fake = _FakeRedis()
        fake.store["hydraulic:worker:heartbeat:simulation"] = "1.0"
        publisher = runtime.RedisHeartbeatPublisher("notification")
        with patch_redis(fake):
            run(publisher.publish_once())
            run(publisher.close())
        self.assertEqual(fake.deleted, ["hydraulic:worker:heartbeat:notification"])
        # The simulation worker's key is still there: this worker never owned it.
        self.assertIn("hydraulic:worker:heartbeat:simulation", fake.store)


class ReadinessTests(unittest.TestCase):
    """``/readyz`` is 200 only on evidence, never on intent."""

    def setUp(self) -> None:
        self.readiness = runtime.WorkerReadiness(worker_id="w-1", component="nowcast")

    def _evaluate(self, *, monotonic: float = 100.0):
        return self.readiness.evaluate(monotonic_now=monotonic, heartbeat_max_age=15.0)

    def test_unready_before_startup_checks_complete(self) -> None:
        ready, reason, _ = self._evaluate()
        self.assertFalse(ready)
        self.assertIs(reason, runtime.StartupFailure.claim_probe_failed)

    def test_unready_without_a_claim_round_trip(self) -> None:
        self.readiness.record_startup(
            runtime.StartupReport(True, runtime.StartupFailure.ready)
        )
        ready, reason, _ = self._evaluate()
        self.assertFalse(ready)
        self.assertIs(reason, runtime.StartupFailure.claim_probe_failed)

    def test_unready_when_the_heartbeat_was_never_published(self) -> None:
        # The Redis publication is the evidence the API reads.  A worker that
        # never published one must not report ready, even with a claim.
        self.readiness.record_startup(
            runtime.StartupReport(True, runtime.StartupFailure.ready)
        )
        self.readiness.record_claim(claimed=True)
        self.readiness.record_heartbeat(monotonic_now=100.0)
        ready, reason, _ = self._evaluate()
        self.assertFalse(ready)
        self.assertIs(reason, runtime.StartupFailure.redis_unavailable)

    def test_ready_only_with_startup_claim_heartbeat_and_publication(self) -> None:
        self.readiness.record_startup(
            runtime.StartupReport(True, runtime.StartupFailure.ready)
        )
        self.readiness.record_claim(claimed=True)
        self.readiness.record_heartbeat(monotonic_now=100.0)
        self.readiness.record_heartbeat_published(published=True)
        ready, reason, detail = self._evaluate()
        self.assertTrue(ready, detail)
        self.assertIs(reason, runtime.StartupFailure.ready)

    def test_stale_heartbeat_fails_closed(self) -> None:
        self.readiness.record_startup(
            runtime.StartupReport(True, runtime.StartupFailure.ready)
        )
        self.readiness.record_claim(claimed=True)
        self.readiness.record_heartbeat_published(published=True)
        self.readiness.record_heartbeat(monotonic_now=100.0)
        ready, reason, _ = self._evaluate(monotonic=200.0)
        self.assertFalse(ready)
        self.assertIs(reason, runtime.StartupFailure.worker_heartbeat_stale)

    def test_shutdown_drops_readiness_immediately(self) -> None:
        self.readiness.record_startup(
            runtime.StartupReport(True, runtime.StartupFailure.ready)
        )
        self.readiness.record_claim(claimed=True)
        self.readiness.record_heartbeat(monotonic_now=100.0)
        self.readiness.record_heartbeat_published(published=True)
        self.assertTrue(self._evaluate()[0])
        self.readiness.begin_shutdown()
        ready, reason, _ = self._evaluate()
        self.assertFalse(ready)
        self.assertIs(reason, runtime.StartupFailure.worker_stopping)


class HealthServerTests(unittest.TestCase):
    """Liveness is the event loop; readiness is the evidence."""

    def setUp(self) -> None:
        self.readiness = runtime.WorkerReadiness(
            worker_id="w-9", component="outbox-relay"
        )
        self.server = runtime.HealthServer(
            self.readiness,
            host="127.0.0.1",
            port=0,
            heartbeat_max_age=15.0,
            component="outbox-relay",
            monotonic=lambda: 100.0,
        )
        self._loop = asyncio.new_event_loop()
        self.addCleanup(self._loop.close)

    def _request(self, path: str) -> tuple[int, dict]:
        async def go() -> tuple[int, dict]:
            await self.server.start()
            try:
                port = self.server.port
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode())
                await writer.drain()
                raw = await asyncio.wait_for(reader.read(), timeout=5.0)
                writer.close()
                await writer.wait_closed()
                head, _, body = raw.partition(b"\r\n\r\n")
                status = int(head.split(b" ")[1])
                return status, json.loads(body)
            finally:
                await self.server.stop()

        return self._loop.run_until_complete(go())

    def test_liveness_is_200_before_any_evidence_exists(self) -> None:
        # /healthz must not require queued work, but it must also not be the
        # readiness signal, so it carries no ready flag.
        status, payload = self._request("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(payload["component"], "outbox-relay")
        self.assertNotIn("ready", payload)

    def test_readiness_is_503_until_the_evidence_is_there(self) -> None:
        status, payload = self._request("/readyz")
        self.assertEqual(status, 503)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["reason"], "claim_probe_failed")

    def test_readiness_turns_200_only_after_full_evidence(self) -> None:
        self.readiness.record_startup(
            runtime.StartupReport(True, runtime.StartupFailure.ready)
        )
        self.readiness.record_claim(claimed=False)
        self.readiness.record_heartbeat(monotonic_now=100.0)
        self.readiness.record_heartbeat_published(published=True)
        status, payload = self._request("/readyz")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ready"])

    def test_unknown_path_is_404(self) -> None:
        status, _ = self._request("/metrics")
        self.assertEqual(status, 404)


class StartupCheckTests(unittest.TestCase):
    def test_first_failing_step_names_its_own_reason(self) -> None:
        steps = (
            runtime.PreflightStep(
                "database",
                lambda: (_ for _ in ()).throw(ConnectionRefusedError("dsn")),
                runtime.StartupFailure.database_unavailable,
            ),
            runtime.PreflightStep(
                "redis", lambda: True, runtime.StartupFailure.redis_unavailable
            ),
        )
        report = run(runtime.run_startup_checks(steps, component="outbox-relay"))
        self.assertFalse(report.ready)
        self.assertIs(report.reason, runtime.StartupFailure.database_unavailable)
        self.assertEqual(report.detail, "ConnectionRefusedError")
        self.assertEqual(dict(report.checks), {"database": False, "redis": False})

    def test_a_driver_error_never_leaks_the_dsn(self) -> None:
        secret = "postgresql://u:hunter2@db/hydraulic"

        def boom() -> None:
            raise RuntimeError(f"could not connect to {secret}")

        report = run(
            runtime.run_startup_checks(
                (
                    runtime.PreflightStep(
                        "database", boom, runtime.StartupFailure.database_unavailable
                    ),
                ),
                component="network-job",
            )
        )
        self.assertNotIn("hunter2", report.detail)
        self.assertEqual(report.detail, "RuntimeError")

    def test_all_steps_passing_yields_ready(self) -> None:
        report = run(
            runtime.run_startup_checks(
                (
                    runtime.PreflightStep(
                        "a", lambda: True, runtime.StartupFailure.ready
                    ),
                    runtime.PreflightStep(
                        "b", lambda: None, runtime.StartupFailure.ready
                    ),
                ),
                component="notification",
            )
        )
        self.assertTrue(report.ready)
        self.assertEqual(dict(report.checks), {"a": True, "b": True})


class RuntimeConfigTests(unittest.TestCase):
    def test_env_prefix_drives_the_health_endpoint(self) -> None:
        config = runtime.WorkerRuntimeConfig.from_environ(
            "outbox-relay",
            {
                "SCN_WORKER_OUTBOX_RELAY_HEALTH_PORT": "8023",
                "SCN_WORKER_OUTBOX_RELAY_IDLE_SECONDS": "0.25",
            },
        )
        self.assertEqual(config.health_port, 8023)
        self.assertEqual(config.idle_seconds, 0.25)
        self.assertEqual(
            config.heartbeat_key, "hydraulic:worker:heartbeat:outbox-relay"
        )

    def test_a_malformed_value_is_a_configuration_error(self) -> None:
        with self.assertRaises(ValueError):
            runtime.WorkerRuntimeConfig.from_environ(
                "nowcast", {"SCN_WORKER_NOWCAST_HEALTH_PORT": "not-a-port"}
            )
        with self.assertRaises(ValueError):
            runtime.WorkerRuntimeConfig.from_environ(
                "nowcast", {"SCN_WORKER_NOWCAST_HEALTH_PORT": "70000"}
            )

    def test_heartbeat_max_age_tracks_the_probe_interval(self) -> None:
        config = runtime.WorkerRuntimeConfig(component="nowcast", probe_seconds=5.0)
        self.assertEqual(config.heartbeat_max_age, 10.0)


class OutboxRelayWorkerTests(unittest.TestCase):
    """The relay round-trip is the claim evidence; a raise is not."""

    def test_an_empty_relay_round_trip_still_counts_as_a_claim(self) -> None:
        calls: list[str] = []

        class Relay:
            async def relay_once(self, publish):
                calls.append("relay")
                return 0

            async def pending_count(self) -> int:
                return 0

        worker = outbox_relay.OutboxRelayWorker(Relay(), lambda envelope: _noop())
        self.assertTrue(run(worker.run_once()))
        self.assertEqual(calls, ["relay"])

    def test_a_relay_failure_propagates_so_readiness_drops(self) -> None:
        class Relay:
            async def relay_once(self, publish):
                raise RuntimeError("Redis is disconnected; outbox remains pending")

            async def pending_count(self) -> int:
                return 0

        worker = outbox_relay.OutboxRelayWorker(Relay(), lambda envelope: _noop())
        with self.assertRaises(RuntimeError):
            run(worker.run_once())

    def test_a_publish_failure_is_not_acknowledged(self) -> None:
        # The relay marks rows published only after the publish returns, so a
        # raising publisher must leave the row pending.
        published: list[int] = []

        class Relay:
            async def relay_once(self, publish):
                await publish({"sequence": 1})
                return 1

            async def pending_count(self) -> int:
                return 1

        async def publish(envelope):
            published.append(envelope["sequence"])
            raise ConnectionError("broker gone")

        worker = outbox_relay.OutboxRelayWorker(Relay(), publish)
        with self.assertRaises(ConnectionError):
            run(worker.run_once())
        self.assertEqual(published, [1])

    def test_published_rows_are_counted(self) -> None:
        class Relay:
            async def relay_once(self, publish):
                return 7

            async def pending_count(self) -> int:
                return 7

        worker = outbox_relay.OutboxRelayWorker(Relay(), lambda envelope: _noop())
        run(worker.run_once())
        self.assertEqual(worker.published, 7)


async def _noop() -> int:
    return 1


class NetworkJobStagedInputTests(unittest.TestCase):
    """A staged payload is only real if its hash matches the job."""

    def _claim(self, job_id, job_type="import", fingerprint=""):
        return network_job_worker.NetworkJobClaim(
            job_id=job_id,
            job_type=job_type,
            attempt_no=1,
            fencing_token=1,
            lease_token=job_id,
            lease_until=None,
            request_fingerprint=fingerprint,
            network_id=None,
            created_by=None,
        )

    def test_a_hash_mismatch_fails_closed_and_non_retryably(self) -> None:
        import hashlib
        import tempfile
        import uuid

        job_id = uuid.uuid4()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / f"{job_id}.inp"
            path.write_bytes(b"[SECTIONS]\n")
            claim = self._claim(
                job_id, fingerprint=hashlib.sha256(b"other").hexdigest()
            )
            with self.assertRaises(network_job_worker.NetworkJobError) as caught:
                network_job_worker.read_staged_input(
                    Path(root), claim, max_bytes=1 << 20
                )
        self.assertEqual(caught.exception.error_code, "STAGED_INPUT_HASH_MISMATCH")
        self.assertFalse(caught.exception.retryable)
        self.assertIn(caught.exception.error_code, network_job_worker.NON_RETRYABLE)

    def test_a_missing_staging_root_fails_closed(self) -> None:
        import tempfile
        import uuid

        job_id = uuid.uuid4()
        with tempfile.TemporaryDirectory() as parent:
            with self.assertRaises(network_job_worker.NetworkJobError) as caught:
                network_job_worker.read_staged_input(
                    Path(parent) / "absent", self._claim(job_id), max_bytes=1 << 20
                )
        self.assertEqual(caught.exception.error_code, "STAGED_INPUT_MISSING")
        self.assertFalse(caught.exception.retryable)

    def test_oversized_input_is_rejected_before_parsing(self) -> None:
        import hashlib
        import tempfile
        import uuid

        job_id = uuid.uuid4()
        payload = b"x" * 64
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / f"{job_id}.inp").write_bytes(payload)
            claim = self._claim(job_id, fingerprint=hashlib.sha256(payload).hexdigest())
            with self.assertRaises(network_job_worker.NetworkJobError) as caught:
                network_job_worker.read_staged_input(Path(root), claim, max_bytes=8)
        self.assertEqual(caught.exception.error_code, "STAGED_INPUT_TOO_LARGE")

    def test_the_staged_path_layout_is_deterministic(self) -> None:
        import uuid

        job_id = uuid.uuid4()
        root = Path("/staging")
        self.assertEqual(
            network_job_worker.staged_input_path(root, job_id, "import"),
            root / f"{job_id}.inp",
        )
        self.assertEqual(
            network_job_worker.staged_input_path(root, job_id, "seed"),
            root / f"{job_id}.json",
        )


class NetworkJobBusinessTests(unittest.TestCase):
    """The business step must run the real parser, not a placeholder."""

    def _claim(self, job_id, job_type):
        return network_job_worker.NetworkJobClaim(
            job_id=job_id,
            job_type=job_type,
            attempt_no=1,
            fencing_token=1,
            lease_token=job_id,
            lease_until=None,
            request_fingerprint="0" * 64,
            network_id=None,
            created_by=None,
        )

    def test_a_real_inp_file_produces_a_real_validation_report(self) -> None:
        import uuid

        fixture = (
            BACKEND
            / "tests"
            / "fixtures"
            / "hydraulic"
            / "imports"
            / "swmm_minimal_wgs84.inp"
        )
        payload = fixture.read_bytes()
        outcome = network_job_worker.compute_outcome(
            self._claim(uuid.uuid4(), "validate"), payload
        )
        self.assertEqual(outcome.kind, "validation")
        self.assertIsNotNone(outcome.report)
        # The report comes from validate_topology, so it carries its own
        # algorithm version and a content hash over the real findings.
        self.assertTrue(outcome.report.algorithm_version)
        self.assertEqual(len(outcome.result_sha256), 64)
        self.assertGreater(outcome.report.pipe_count, 0)
        self.assertEqual(outcome.source_bytes, payload)

    def test_a_real_inp_file_produces_a_real_import_record(self) -> None:
        import uuid

        fixture = (
            BACKEND
            / "tests"
            / "fixtures"
            / "hydraulic"
            / "imports"
            / "swmm_minimal_wgs84.inp"
        )
        outcome = network_job_worker.compute_outcome(
            self._claim(uuid.uuid4(), "import"), fixture.read_bytes()
        )
        self.assertEqual(outcome.kind, "import")
        self.assertEqual(outcome.source_format, "swmm-inp-parser-v1")
        self.assertGreaterEqual(outcome.counts["conduits"], 1)

    def test_a_seed_manifest_drives_the_real_generator(self) -> None:
        import uuid

        manifest = json.dumps({"n": 2, "m": 3, "k": 1, "random_seed": 7}).encode()
        outcome = network_job_worker.compute_outcome(
            self._claim(uuid.uuid4(), "seed"), manifest
        )
        self.assertEqual(outcome.kind, "import")
        self.assertEqual(outcome.source_format, "seed-v1")
        # N*M+1 nodes and N*M+K pipes for the declared controls.
        self.assertEqual(outcome.counts["nodes"], 2 * 3 + 1)
        self.assertEqual(outcome.counts["pipes"], 2 * 3 + 1)

    def test_an_incomplete_seed_manifest_is_non_retryable(self) -> None:
        import uuid

        with self.assertRaises(network_job_worker.NetworkJobError) as caught:
            network_job_worker.compute_outcome(
                self._claim(uuid.uuid4(), "seed"), b'{"n": 2}'
            )
        self.assertEqual(caught.exception.error_code, "SEED_MANIFEST_INVALID")
        self.assertFalse(caught.exception.retryable)

    def test_a_malformed_inp_document_is_rejected_by_the_parser(self) -> None:
        import uuid

        with self.assertRaises(Exception):
            network_job_worker.compute_outcome(
                self._claim(uuid.uuid4(), "import"), b"not an inp document at all"
            )


class NetworkJobRetryPolicyTests(unittest.TestCase):
    def test_backoff_follows_the_configured_ladder(self) -> None:
        repo = network_job_worker.SessionNetworkJobRepository(
            None,
            lease_seconds=30,
            attempt_timeout_seconds=600,
            max_attempts=3,
            retry_ladder=[10, 60],
        )
        self.assertEqual(repo.backoff_seconds(1), 10)
        self.assertEqual(repo.backoff_seconds(2), 60)
        # Beyond the ladder the last rung repeats rather than raising.
        self.assertEqual(repo.backoff_seconds(9), 60)

    def test_an_empty_ladder_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            network_job_worker.SessionNetworkJobRepository(
                None,
                lease_seconds=30,
                attempt_timeout_seconds=600,
                max_attempts=3,
                retry_ladder=[],
            )


class NowcastPolicyGatingTests(unittest.TestCase):
    """A run may only be created against a verified policy config."""

    def test_bindings_come_from_the_config_not_from_a_constant(self) -> None:
        config = {
            "schema_version": 1,
            "networks": {
                "11111111-1111-4111-8111-111111111111": {
                    "probability_calibration_ids": {
                        "flood_forecast": "22222222-2222-4222-8222-222222222222",
                        "pipe_surcharge": "33333333-3333-4333-8333-333333333333",
                    },
                    "forecast_rule_ids": {
                        "flood_forecast": "44444444-4444-4444-8444-444444444444",
                        "pipe_surcharge": "55555555-5555-4555-8555-555555555555",
                    },
                }
            },
        }
        import uuid

        bindings = nowcast_worker.policy_bindings_for(
            config, uuid.UUID("11111111-1111-4111-8111-111111111111")
        )
        self.assertEqual(
            set(bindings),
            {
                "flood_forecast_probability_calibration_id",
                "pipe_surcharge_probability_calibration_id",
                "flood_forecast_rule_id",
                "pipe_surcharge_rule_id",
            },
        )
        self.assertEqual(
            bindings["flood_forecast_rule_id"], "44444444-4444-4444-8444-444444444444"
        )

    def test_a_network_without_a_config_entry_cannot_be_scheduled(self) -> None:
        import uuid

        with self.assertRaises(nowcast_worker.NowcastWorkerError) as caught:
            nowcast_worker.policy_bindings_for(
                {"schema_version": 1, "networks": {}},
                uuid.UUID("11111111-1111-4111-8111-111111111111"),
            )
        self.assertEqual(
            caught.exception.error_code, nowcast_worker.NOWCAST_MISSING_POLICY
        )
        self.assertFalse(caught.exception.retryable)

    def test_an_unloadable_config_creates_no_run(self) -> None:
        scheduler = nowcast_worker.SchedulerSingleton(
            lambda session: None,
            lambda: None,
            interval_minutes=10,
            horizon_minutes=120,
            step_minutes=5,
            deadline_seconds=180,
            slot_timeout_seconds=180.0,
        )
        with mock.patch.object(
            nowcast_worker, "load_forecast_policy", return_value=None
        ):
            self.assertEqual(run(scheduler.tick()), 0)
        self.assertEqual(scheduler.scheduled, 0)
        self.assertEqual(scheduler.policy_unavailable, 1)

    def test_a_missing_lineage_creates_no_run(self) -> None:
        import uuid

        config = {
            "schema_version": 1,
            "networks": {
                "11111111-1111-4111-8111-111111111111": {
                    "probability_calibration_ids": {
                        "flood_forecast": "22222222-2222-4222-8222-222222222222",
                        "pipe_surcharge": "33333333-3333-4333-8333-333333333333",
                    },
                    "forecast_rule_ids": {
                        "flood_forecast": "44444444-4444-4444-8444-444444444444",
                        "pipe_surcharge": "55555555-5555-4555-8555-555555555555",
                    },
                }
            },
        }
        scheduler = nowcast_worker.SchedulerSingleton(
            lambda session: None,
            lambda: None,
            interval_minutes=10,
            horizon_minutes=120,
            step_minutes=5,
            deadline_seconds=180,
            slot_timeout_seconds=180.0,
        )
        seen: list[str] = []

        class Repo:
            def __init__(self) -> None:
                self.session = mock.MagicMock()
                self.session.scalar = self._scalar
                self.max_attempts = 3
                self.lease_seconds = 30

            async def _scalar(self, *args, **kwargs):
                return True

            async def schedulable_networks(self):
                return (
                    {
                        "network_id": uuid.UUID("11111111-1111-4111-8111-111111111111"),
                        "network_revision_id": uuid.uuid4(),
                        "target_manifest_id": uuid.uuid4(),
                        "target_manifest_sha256": "a" * 64,
                    },
                )

            async def resolve_lineage(self, network, *, horizon_minutes):
                seen.append(str(network["network_id"]))
                return None  # no completed simulation / rainfall scenario

        repo = Repo()

        async def in_transaction(action):
            return await action(repo)

        scheduler._in_transaction = in_transaction
        with mock.patch.object(
            nowcast_worker, "load_forecast_policy", return_value=config
        ):
            self.assertEqual(run(scheduler.tick()), 0)
        self.assertEqual(len(seen), 1)
        self.assertEqual(scheduler.lineage_unavailable, 1)
        self.assertEqual(scheduler.scheduled, 0)


class NowcastRunnerCommandTests(unittest.TestCase):
    """An absent materializer must stop startup, not fake a forecast."""

    def test_an_empty_command_is_rejected(self) -> None:
        with self.assertRaises(nowcast_worker.NowcastWorkerError) as caught:
            nowcast_worker.resolve_runner_command({})
        self.assertEqual(caught.exception.error_code, "runner_command_unavailable")
        self.assertFalse(caught.exception.retryable)

    def test_a_comma_list_and_a_json_array_agree(self) -> None:
        self.assertEqual(
            nowcast_worker.parse_runner_argv("python,-m,app.x"),
            ("python", "-m", "app.x"),
        )
        self.assertEqual(
            nowcast_worker.parse_runner_argv('["python", "-m", "app.x"]'),
            ("python", "-m", "app.x"),
        )

    def test_a_module_that_does_not_exist_is_rejected(self) -> None:
        with self.assertRaises(nowcast_worker.NowcastWorkerError) as caught:
            nowcast_worker.probe_runner_executable(
                ("python", "-m", "app.services.forecast.does_not_exist")
            )
        self.assertEqual(caught.exception.error_code, "runner_command_unavailable")

    def test_the_shipped_runner_module_resolves(self) -> None:
        # The Compose default must pass preflight, otherwise the service would
        # crash-loop for a reason that has nothing to do with the database.
        nowcast_worker.probe_runner_executable(
            ("python", "-m", "app.workers.nowcast_materializer")
        )

    def test_a_binary_off_path_is_rejected(self) -> None:
        with self.assertRaises(nowcast_worker.NowcastWorkerError):
            nowcast_worker.probe_runner_executable(("/nonexistent/runner",))


class NowcastMaterializerTests(unittest.TestCase):
    """The default runner verifies, then refuses — it never invents a forecast."""

    def setUp(self) -> None:
        from app.workers import nowcast_materializer

        self.mod = nowcast_materializer
        self.repo_root = BACKEND.parent / "docker" / "hydraulic"

    def _request(self) -> dict:
        return {
            "run_id": "3f1a0c2e-0000-4000-8000-000000000001",
            "network_id": "11111111-1111-4111-8111-111111111111",
            "deadline_at": "2999-01-01T00:00:00+00:00",
            "policy_bindings": {
                "flood_forecast_rule_id": "44444444-4444-4444-8444-444444444444",
                "flood_forecast_probability_calibration_id": (
                    "22222222-2222-4222-8222-222222222222"
                ),
                "pipe_surcharge_rule_id": "55555555-5555-4555-8555-555555555555",
                "pipe_surcharge_probability_calibration_id": (
                    "33333333-3333-4333-8333-333333333333"
                ),
            },
        }

    def test_it_refuses_instead_of_returning_a_success_envelope(self) -> None:
        from app.core.config import settings

        with mock.patch.multiple(
            settings,
            HYDRAULIC_ARTIFACT_ROOT=str(self.repo_root / "artifacts"),
            FORECAST_POLICY_CONFIG_PATH=str(
                self.repo_root / "forecast-policy" / "forecast-policy.json"
            ),
        ):
            # verify() raises SystemExit only on a real policy problem.  With the
            # shipped artifacts in place it must return normally, proving every
            # bound artifact resolved and re-hashed; main() is what then reports
            # the absent materialization layer.
            self.mod.verify(self._request())

    def test_main_exits_non_zero_and_names_the_missing_materializer(self) -> None:
        import io

        from app.core.config import settings

        errors: list[str] = []
        with mock.patch.multiple(
            settings,
            HYDRAULIC_ARTIFACT_ROOT=str(self.repo_root / "artifacts"),
            FORECAST_POLICY_CONFIG_PATH=str(
                self.repo_root / "forecast-policy" / "forecast-policy.json"
            ),
        ):
            with mock.patch("sys.stdin", io.StringIO(json.dumps(self._request()))):
                with mock.patch("sys.stderr", io.StringIO()) as stderr:
                    code = self.mod.main([])
        errors = stderr.getvalue().splitlines()
        self.assertEqual(code, self.mod.EXIT_MATERIALIZER_UNAVAILABLE)
        self.assertIn(self.mod.ERROR_MATERIALIZER_UNAVAILABLE, "\n".join(errors))
        # It must not have printed a success envelope.
        self.assertNotIn("result_sha256", "\n".join(errors))

    def test_a_tampered_artifact_is_detected(self) -> None:
        import shutil
        import tempfile

        from app.core.config import settings

        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(self.repo_root / "artifacts", Path(tmp) / "artifacts")
            target = Path(tmp) / "artifacts" / "flood_forecast_rule_v1.json"
            target.write_text(
                target.read_text(encoding="utf-8") + " ", encoding="utf-8"
            )
            with mock.patch.multiple(
                settings,
                HYDRAULIC_ARTIFACT_ROOT=str(Path(tmp) / "artifacts"),
                FORECAST_POLICY_CONFIG_PATH=str(
                    self.repo_root / "forecast-policy" / "forecast-policy.json"
                ),
            ):
                with self.assertRaises(SystemExit) as caught:
                    self.mod.verify(self._request())
            self.assertEqual(
                str(caught.exception),
                f"{self.mod.ERROR_ARTIFACT_HASH_MISMATCH}: {target.name}",
            )

    def test_an_artifact_outside_the_registry_is_reported_missing(self) -> None:
        import tempfile

        from app.core.config import settings

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.multiple(
                settings,
                HYDRAULIC_ARTIFACT_ROOT=tmp,
                FORECAST_POLICY_CONFIG_PATH=str(
                    self.repo_root / "forecast-policy" / "forecast-policy.json"
                ),
            ):
                with self.assertRaises(SystemExit) as caught:
                    self.mod.verify(self._request())
            self.assertIn(self.mod.ERROR_ARTIFACT_MISSING, str(caught.exception))


class NotificationOutcomeTests(unittest.TestCase):
    def test_a_clean_fan_out_settles_as_fully_succeeded(self) -> None:
        outcome = notification_worker.outcome_from_result(
            {
                "event_id": "e-1",
                "event_type": "alert.created",
                "email_attempted": 2,
                "email_succeeded": 2,
                "email_failed": 0,
                "webhook_attempted": 1,
                "webhook_succeeded": 1,
                "webhook_failed": 0,
            }
        )
        self.assertTrue(outcome.fully_succeeded)
        self.assertFalse(outcome.partially_succeeded)
        self.assertEqual(outcome.errors, [])

    def test_a_total_failure_settles_as_not_delivered(self) -> None:
        outcome = notification_worker.outcome_from_result(
            {
                "event_id": "e-2",
                "event_type": "alert.created",
                "email_attempted": 1,
                "email_succeeded": 0,
                "email_failed": 1,
                "webhook_attempted": 0,
                "webhook_succeeded": 0,
                "webhook_failed": 0,
            }
        )
        self.assertFalse(outcome.fully_succeeded)
        self.assertFalse(outcome.partially_succeeded)
        self.assertIn("SMTP_DELIVERY_FAILED", outcome.errors)

    def test_a_partial_success_is_not_re_sent(self) -> None:
        outcome = notification_worker.outcome_from_result(
            {
                "event_id": "e-3",
                "event_type": "alert.created",
                "email_attempted": 2,
                "email_succeeded": 1,
                "email_failed": 1,
                "webhook_attempted": 0,
                "webhook_succeeded": 0,
                "webhook_failed": 0,
            }
        )
        self.assertFalse(outcome.fully_succeeded)
        self.assertTrue(outcome.partially_succeeded)

    def test_a_missing_event_dead_letters_rather_than_reconstructing(self) -> None:
        import uuid

        claim = notification_worker.DeliveryClaim(
            delivery_id=uuid.uuid4(),
            event_id=uuid.uuid4(),
            alert_id=uuid.uuid4(),
            channel="email",
            destination_id="ops@example.com",
            template_version="v1",
            attempt_count=1,
            lease_until=None,
            lease_owner="w-1",
        )

        class Repo:
            async def load_event(self, event_id):
                return None

        worker = notification_worker.NotificationWorker(
            lambda session: Repo(),
            lambda: None,
            object(),
            worker_id="w-1",
        )

        async def in_transaction(action):
            return await action(Repo())

        worker._in_transaction = in_transaction
        with self.assertRaises(notification_worker.NotificationWorkerError) as caught:
            run(worker._attempt([claim]))
        self.assertEqual(caught.exception.error_code, "EVENT_EXPIRED")
        self.assertFalse(caught.exception.retryable)
        self.assertIn(caught.exception.error_code, notification_worker.NON_RETRYABLE)

    def test_the_transport_check_rejects_an_unconfigured_worker(self) -> None:
        from app.core.config import settings

        with mock.patch.multiple(
            settings,
            SMTP_ENABLED=False,
            ALERT_EMAIL_RECIPIENTS=[],
            ALERT_WEBHOOK_URLS=[],
        ):
            with self.assertRaises(RuntimeError):
                notification_worker._probe_transport()

    def test_the_transport_check_accepts_a_configured_webhook(self) -> None:
        from app.core.config import settings

        with mock.patch.multiple(
            settings,
            SMTP_ENABLED=False,
            ALERT_EMAIL_RECIPIENTS=[],
            ALERT_WEBHOOK_URLS=["https://hooks.example.com/forecast"],
        ):
            notification_worker._probe_transport()


class ReservedLaneTests(unittest.TestCase):
    """The nowcast lane is slot 2 and is never lent to the general lane."""

    def test_the_reserved_slot_is_slot_two(self) -> None:
        self.assertEqual(nowcast_worker.NOWCAST_SLOT_ID, 2)

    def test_the_scheduler_lock_is_distinct_from_every_other_lane(self) -> None:
        from app.workers.simulation_worker import WORKER_HEARTBEAT_KEY  # noqa: F401

        locks = {
            "nowcast": nowcast_worker.NOWCAST_SCHEDULER_LOCK_KEY,
            "network-job": network_job_worker.NETWORK_JOB_CLAIM_LOCK_KEY,
            "notification": notification_worker.NOTIFICATION_CLAIM_LOCK_KEY,
        }
        # The outbox relay lock lives in app.main (0x4859444F) and the
        # simulation claim lock in app.services.hydraulic.tasks (0x48594452).
        self.assertNotIn(0x4859444F, locks.values())
        self.assertNotIn(0x48594452, locks.values())
        self.assertEqual(len(set(locks.values())), len(locks))


if __name__ == "__main__":
    unittest.main()
