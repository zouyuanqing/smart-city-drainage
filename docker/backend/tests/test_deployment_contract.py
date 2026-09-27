from __future__ import annotations

import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HEALTHCHECK = ROOT / "docker" / "backend" / "healthcheck.py"
COMPOSE = ROOT / "docker-compose.yml"


class _ProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        status = 200 if self.path == "/readyz" else 503
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format: str, *args: object) -> None:
        return


class HealthcheckScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_accepts_http_200(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(HEALTHCHECK),
                "--url",
                f"{self.base_url}/readyz",
                "--timeout",
                "2",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_http_503(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(HEALTHCHECK),
                "--url",
                f"{self.base_url}/not-ready",
                "--timeout",
                "2",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("HTTP 503", result.stderr)


class DeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compose = COMPOSE.read_text(encoding="utf-8")
        cls.cpu_dockerfile = (ROOT / "docker" / "backend" / "Dockerfile.cpu").read_text(
            encoding="utf-8"
        )
        cls.gpu_dockerfile = (ROOT / "docker" / "backend" / "Dockerfile").read_text(
            encoding="utf-8"
        )
        cls.frontend_dockerfile = (
            ROOT / "docker" / "frontend" / "Dockerfile"
        ).read_text(encoding="utf-8")
        cls.proxy_nginx = (
            ROOT / "docker" / "nginx" / "conf.d" / "default.conf"
        ).read_text(encoding="utf-8")
        cls.frontend_nginx = (
            ROOT / "docker" / "frontend" / "nginx-default.conf"
        ).read_text(encoding="utf-8")

    def _service_block(self, service: str) -> str:
        """The YAML body of one top-level service, indentation-scoped.

        Assertions about "this service must do X" are meaningless if they can be
        satisfied by some other service's stanza, so every per-service
        assertion below reads this block rather than the whole file.
        """

        marker = f"\n  {service}:\n"
        start = self.compose.index(marker) + len(marker)
        lines: list[str] = []
        for line in self.compose[start:].splitlines(keepends=True):
            if line.strip() and not line.startswith("    "):
                break
            lines.append(line)
        self.assertTrue(lines, f"service {service} has an empty body")
        return "".join(lines)

    def test_database_url_is_supplied_by_the_environment_never_inline(self) -> None:
        # The frozen contract forbids default credentials, so the compose file
        # must carry no DSN at all: neither the raw reserved-password bytes nor
        # an inline percent-encoded copy. The encoded URL is supplied by the
        # deployment environment, which is where this test reads it from.
        self.assertNotIn(
            "postgresql+asyncpg://drainage_admin:Dr@inage_S3cur3_2024!@postgres",
            self.compose,
        )
        self.assertNotIn(
            "postgresql+asyncpg://drainage_admin:Dr%40inage_S3cur3_2024%21@postgres",
            self.compose,
        )
        self.assertNotIn("postgresql+asyncpg://", self.compose)
        self.assertNotIn("postgresql://", self.compose)
        for variable in ("DATABASE_URL", "DATABASE_URL_SYNC"):
            self.assertIn(
                f"{variable}: ${{{variable}:?set {variable} to the encoded PostgreSQL URL}}",
                self.compose,
            )
        # Every process that talks to PostgreSQL must receive the same
        # environment-supplied URL, so the encoding stays a deployment concern.
        # That is migrate, the API, and all five worker services.
        self.assertEqual(
            self.compose.count("DATABASE_URL: ${DATABASE_URL:?"),
            7,
            "migrate, backend and the five workers share one env-supplied DSN",
        )
        self.assertEqual(
            self.compose.count("DATABASE_URL_SYNC: ${DATABASE_URL_SYNC:?"),
            1,
        )

    def test_simulation_worker_is_a_real_module_entrypoint(self) -> None:
        self.assertIn("simulation-worker:", self.compose)
        self.assertIn(
            'command: ["python", "-m", "app.workers.simulation_worker"]', self.compose
        )
        self.assertIn("HYDRAULIC_SANDBOX_ROOT: /var/lib/smart-drainage/sandbox", self.compose)
        self.assertIn("HYDRAULIC_EXPORT_ROOT: /var/lib/smart-drainage/results", self.compose)
        self.assertIn("simulation_sandbox:", self.compose)
        self.assertIn("simulation_results:", self.compose)

    def test_worker_is_non_root_bounded_and_readiness_checked(self) -> None:
        self.assertIn('user: "10001:10001"', self.compose)
        self.assertIn("read_only: true", self.compose)
        self.assertIn("cap_drop:\n      - ALL", self.compose)
        self.assertIn("no-new-privileges:true", self.compose)
        self.assertIn("mem_limit: 4g", self.compose)
        self.assertIn("cpus: 2.0", self.compose)
        self.assertIn("pids_limit: 512", self.compose)
        self.assertIn("http://127.0.0.1:8010/readyz", self.compose)

    def test_backend_images_create_writable_logs_and_drop_root(self) -> None:
        for dockerfile in (self.cpu_dockerfile, self.gpu_dockerfile):
            self.assertIn("useradd --uid 10001", dockerfile)
            self.assertIn("mkdir -p /app/logs", dockerfile)
            self.assertIn("USER 10001:10001", dockerfile)
            self.assertIn("http://127.0.0.1:8000/health", dockerfile)

    def test_every_worker_service_is_a_real_module_entrypoint(self) -> None:
        # Each service must run the module that does the work, not a shell
        # sleep or a stub that only answers 200.
        expected = {
            "simulation-worker": "app.workers.simulation_worker",
            "network-job-worker": "app.workers.network_job_worker",
            "nowcast-worker": "app.workers.nowcast_worker",
            "outbox-relay": "app.workers.outbox_relay",
            "notification-worker": "app.workers.notification_worker",
        }
        for service, module in expected.items():
            self.assertIn(f"\n  {service}:", self.compose, f"{service} is not declared")
            self.assertIn(
                f'command: ["python", "-m", "{module}"]',
                self.compose,
                f"{service} does not execute {module}",
            )
            # Non-root, read-only root, no capabilities, bounded resources.
            block = self._service_block(service)
            self.assertIn('user: "10001:10001"', block)
            self.assertIn("read_only: true", block)
            self.assertIn("cap_drop:\n      - ALL", block)
            self.assertIn("no-new-privileges:true", block)
            self.assertIn("mem_limit:", block)
            self.assertIn("cpus:", block)
            self.assertIn("pids_limit:", block)
            self.assertIn("stop_grace_period: 30s", block)
            # A worker that cannot be probed cannot gate anything.
            self.assertIn("/readyz", block)
            # The healthcheck must be the real probe script, not a curl.
            self.assertIn("python\", \"/opt/scn/healthcheck.py\"", block)

    def test_worker_services_each_probe_their_own_port(self) -> None:
        # A shared port would let one healthy worker vouch for another, so the
        # five workers must bind five distinct ports and probe each of them.
        ports = {
            "simulation-worker": 8010,
            "network-job-worker": 8021,
            "nowcast-worker": 8022,
            "outbox-relay": 8023,
            "notification-worker": 8024,
        }
        for service, port in ports.items():
            block = self._service_block(service)
            self.assertIn(
                f'http://127.0.0.1:{port}/readyz',
                block,
                f"{service} does not probe its own /readyz on {port}",
            )
        self.assertEqual(
            len(set(ports.values())), len(ports), "worker health ports must be distinct"
        )

    def test_every_worker_depends_on_migrate_and_redis(self) -> None:
        # migrate must have committed before any worker claims a row, and Redis
        # must be reachable before any worker publishes its heartbeat.  A worker
        # that started earlier would claim work it cannot verify.
        for service in (
            "simulation-worker",
            "network-job-worker",
            "nowcast-worker",
            "outbox-relay",
            "notification-worker",
        ):
            block = self._service_block(service)
            self.assertIn("migrate:", block, f"{service} does not wait for migrate")
            self.assertIn(
                "condition: service_completed_successfully",
                block,
                f"{service} does not require a successful migration",
            )
            self.assertIn("redis:", block, f"{service} does not wait for redis")
            self.assertIn(
                "condition: service_healthy",
                block,
                f"{service} does not require a healthy redis",
            )
            self.assertIn(
                "REDIS_URL: ${REDIS_URL:?",
                block,
                f"{service} has no Redis URL, so it cannot publish a heartbeat",
            )

    def test_nginx_waits_for_every_worker(self) -> None:
        # The API's /readyz already fails closed on a stale worker heartbeat, so
        # serving traffic before the workers are up would expose a stack whose
        # own readiness says it is not ready.
        block = self._service_block("nginx")
        for service in (
            "backend",
            "simulation-worker",
            "network-job-worker",
            "nowcast-worker",
            "outbox-relay",
            "notification-worker",
        ):
            self.assertIn(
                f"{service}:",
                block,
                f"nginx does not wait for {service}",
            )

    def test_forecast_policy_artifact_is_mounted_where_the_path_points(self) -> None:
        # FORECAST_POLICY_CONFIG_PATH must resolve inside a real read-only
        # mount, and its sidecar must be present next to it, or the closed-schema
        # hash check in the API and the nowcast worker can never pass.
        for service in ("backend", "nowcast-worker"):
            block = self._service_block(service)
            self.assertIn(
                "FORECAST_POLICY_CONFIG_PATH: "
                "/opt/scn/hydraulic/forecast-policy/forecast-policy.json",
                block,
                f"{service} does not name the shipped policy config",
            )
            self.assertIn(
                "HYDRAULIC_ARTIFACT_ROOT: /opt/scn/hydraulic/artifacts",
                block,
                f"{service} does not name the controlled artifact root",
            )
            self.assertIn(
                "./docker/hydraulic:/opt/scn/hydraulic:ro",
                block,
                f"{service} does not mount the policy root read-only",
            )
        policy = ROOT / "docker" / "hydraulic" / "forecast-policy" / "forecast-policy.json"
        sidecar = ROOT / "docker" / "hydraulic" / "forecast-policy" / (
            "forecast-policy.json.sha256"
        )
        self.assertTrue(policy.is_file(), "the shipped policy config is missing")
        self.assertTrue(sidecar.is_file(), "the policy config sidecar is missing")
        self.assertEqual(len(sidecar.read_text(encoding="utf-8").strip()), 64)
        self.assertNotIn("\n", sidecar.read_text(encoding="utf-8").strip())

    def test_network_job_worker_owns_a_writable_staging_volume(self) -> None:
        # The staging root is created in the image so a fresh named volume
        # inherits the non-root ownership; without it the fail-closed
        # staging_root_not_writable check would trip on a clean stack.
        block = self._service_block("network-job-worker")
        self.assertIn("HYDRAULIC_IMPORT_ROOT: /var/lib/smart-drainage/import", block)
        self.assertIn("network_job_import:/var/lib/smart-drainage/import", block)
        self.assertIn("network_job_import:", self.compose)
        for dockerfile in (self.cpu_dockerfile, self.gpu_dockerfile):
            self.assertIn("/var/lib/smart-drainage/import", dockerfile)

    def test_nowcast_worker_requires_a_resolvable_runner_command(self) -> None:
        # An empty runner command is the honest "no materializer is wired"
        # state, and the worker must refuse to start rather than claim runs it
        # cannot produce.  The default must therefore name a real importable
        # module rather than a blank or a sleep.
        block = self._service_block("nowcast-worker")
        self.assertIn("NOWCAST_RUNNER_COMMAND: ${NOWCAST_RUNNER_COMMAND:-", block)
        self.assertIn("app.workers.nowcast_materializer", block)
        self.assertNotIn('NOWCAST_RUNNER_COMMAND: ""', self.compose)

    def test_notification_worker_declares_a_real_transport(self) -> None:
        # With neither SMTP nor a webhook destination the worker has nothing to
        # deliver, so the service must pass the transport configuration through
        # and fail closed rather than publishing a heartbeat for nothing.
        block = self._service_block("notification-worker")
        for variable in (
            "SMTP_ENABLED",
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USE_TLS",
            "SMTP_USERNAME",
            "SMTP_PASSWORD",
            "SMTP_FROM",
            "ALERT_EMAIL_RECIPIENTS",
            "ALERT_WEBHOOK_URLS",
            "NOTIFICATION_MAX_ATTEMPTS",
        ):
            self.assertIn(f"{variable}:", block, f"notification-worker drops {variable}")

    def test_backend_images_own_the_worker_sandbox_and_result_roots(self) -> None:
        # A named volume whose mount point is missing from the image is created
        # root-owned, so the 10001:10001 simulation worker would fail its
        # fail-closed `workspace_not_writable` startup check on a fresh stack.
        for dockerfile in (self.cpu_dockerfile, self.gpu_dockerfile):
            self.assertIn("/var/lib/smart-drainage/sandbox", dockerfile)
            self.assertIn("/var/lib/smart-drainage/results", dockerfile)
            self.assertIn("chown -R scn:scn /app/logs", dockerfile)
            self.assertIn("/var/lib/smart-drainage", dockerfile)

    def test_frontend_build_reads_owned_deployment_context(self) -> None:
        self.assertIn("scn_frontend_deploy: ./docker/frontend", self.compose)
        self.assertIn(
            "COPY --from=scn_frontend_deploy nginx.conf /etc/nginx/nginx.conf",
            self.frontend_dockerfile,
        )

    def test_redis_probe_authenticates_without_changing_redis_url(self) -> None:
        self.assertIn("REDISCLI_AUTH=", self.compose)
        self.assertIn("redis-cli --raw ping", self.compose)
        # Same rule as the database DSN: the tracked compose file holds no
        # credential, it requires REDIS_URL from the environment instead of
        # building one out of REDIS_PASSWORD.
        self.assertNotIn("redis://:", self.compose)
        self.assertNotIn("${REDIS_PASSWORD:-", self.compose)
        self.assertIn(
            "REDIS_URL: ${REDIS_URL:?set REDIS_URL with the Redis password}",
            self.compose,
        )

    def test_nginx_configs_disable_forecast_sse_buffering(self) -> None:
        for config in (self.proxy_nginx, self.frontend_nginx):
            self.assertIn("location = /api/forecast/events", config)
            self.assertIn("proxy_set_header Authorization $http_authorization;", config)
            self.assertIn("proxy_buffering off;", config)
            self.assertIn("proxy_cache off;", config)
            self.assertIn("gzip off;", config)
            self.assertIn("add_header X-Accel-Buffering no always;", config)

    def test_nginx_configs_expose_health_and_run_on_unprivileged_port(self) -> None:
        for config in (self.proxy_nginx, self.frontend_nginx):
            self.assertIn("listen 8080;", config)
            self.assertIn("location = /nginx-health", config)


if __name__ == "__main__":
    unittest.main()
