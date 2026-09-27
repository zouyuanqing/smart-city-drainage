"""Policy load tests for the shipped ``FORECAST_POLICY_CONFIG_PATH`` artifact.

The contract fixes the closed schema and the sidecar hash rule
(``IMPLEMENTATION_CONTRACT.md`` §forecast policy config).  These tests run the
*real* loader — ``app.main._load_forecast_policy_config``, the same function the
API's ``/readyz`` and the nowcast worker call — against the artifact Compose
mounts, so a config that this suite accepts is one the running system accepts.

The negative cases matter as much as the positive one: a config that loads only
because the test was lenient is a config that would let the system schedule
against unproven policy lineage.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import _load_forecast_policy_config  # noqa: E402
from app.workers.nowcast_worker import load_forecast_policy  # noqa: E402

REPO = BACKEND.parent
POLICY_ROOT = REPO / "docker" / "hydraulic" / "forecast-policy"
POLICY_CONFIG = POLICY_ROOT / "forecast-policy.json"
POLICY_SIDECAR = POLICY_ROOT / "forecast-policy.json.sha256"
ARTIFACT_ROOT = REPO / "docker" / "hydraulic" / "artifacts"


def canonical_bytes(payload: dict) -> bytes:
    """The §1.2 canonical form the contract hashes."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class ShippedPolicyArtifactTests(unittest.TestCase):
    """The artifact Compose mounts must satisfy the frozen rules."""

    def setUp(self) -> None:
        self.assertTrue(POLICY_CONFIG.is_file(), f"missing {POLICY_CONFIG}")
        self.assertTrue(POLICY_SIDECAR.is_file(), f"missing {POLICY_SIDECAR}")

    def _load(self, config_path: Path):
        with mock.patch(
            "app.main.settings.FORECAST_POLICY_CONFIG_PATH", str(config_path)
        ):
            return _load_forecast_policy_config()

    def test_the_shipped_config_loads_with_the_real_loader(self) -> None:
        payload = self._load(POLICY_CONFIG)
        self.assertIsNotNone(payload, "the shipped policy config did not load")
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["networks"])

    def test_the_nowcast_worker_agrees_with_the_api_loader(self) -> None:
        # The worker must not use a laxer loader than the API; if the two ever
        # disagree, one of them would schedule against unverified policy.
        with mock.patch(
            "app.main.settings.FORECAST_POLICY_CONFIG_PATH", str(POLICY_CONFIG)
        ):
            api_view = _load_forecast_policy_config()
        with mock.patch(
            "app.main.settings.FORECAST_POLICY_CONFIG_PATH", str(POLICY_CONFIG)
        ):
            worker_view = load_forecast_policy()
        self.assertEqual(api_view, worker_view)

    def test_the_sidecar_is_exactly_the_canonical_digest(self) -> None:
        payload = json.loads(POLICY_CONFIG.read_text(encoding="utf-8"))
        expected = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        self.assertEqual(POLICY_SIDECAR.read_text(encoding="utf-8").strip(), expected)

    def test_the_sidecar_has_no_second_line_or_extra_token(self) -> None:
        raw = POLICY_SIDECAR.read_text(encoding="utf-8")
        self.assertNotIn("\n", raw.strip())
        self.assertEqual(len(raw.strip()), 64)
        self.assertFalse(raw.strip("0123456789abcdef"))

    def test_the_schema_is_closed_at_every_level(self) -> None:
        payload = self._load(POLICY_CONFIG)
        self.assertEqual(set(payload), {"schema_version", "networks"})
        for network_id, entry in payload["networks"].items():
            self.assertEqual(
                set(entry), {"probability_calibration_ids", "forecast_rule_ids"}
            )
            for group in entry.values():
                self.assertEqual(set(group), {"flood_forecast", "pipe_surcharge"})
            # A UUID key, not an arbitrary string.
            uuid_module = __import__("uuid")
            uuid_module.UUID(network_id)

    def test_the_config_carries_no_self_referential_hash(self) -> None:
        # The contract forbids content_sha256 or any other self-reference in
        # the file: the sidecar is the only place the hash lives.
        raw = POLICY_CONFIG.read_text(encoding="utf-8")
        self.assertNotIn("content_sha256", raw)
        self.assertNotIn("sha256", raw)

    def test_reformatting_the_file_does_not_change_the_digest(self) -> None:
        # The hash is over the canonical form, not the file bytes, so a
        # pretty-printer must not invalidate a correct sidecar.
        payload = json.loads(POLICY_CONFIG.read_text(encoding="utf-8"))
        expected = hashlib.sha256(canonical_bytes(payload)).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            reformatted = Path(tmp) / "policy.json"
            reformatted.write_text(json.dumps(payload, indent=4), encoding="utf-8")
            Path(str(reformatted) + ".sha256").write_text(expected, encoding="utf-8")
            with mock.patch(
                "app.main.settings.FORECAST_POLICY_CONFIG_PATH", str(reformatted)
            ):
                self.assertEqual(_load_forecast_policy_config(), payload)


class PolicyRejectionTests(unittest.TestCase):
    """Every deviation from the closed schema must fail closed."""

    def setUp(self) -> None:
        self.payload = json.loads(POLICY_CONFIG.read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _write(self, payload, *, sidecar: str | None = None, name="policy.json"):
        config = self.root / name
        config.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        digest = (
            sidecar
            if sidecar is not None
            else hashlib.sha256(canonical_bytes(payload)).hexdigest()
        )
        Path(str(config) + ".sha256").write_text(digest, encoding="utf-8")
        return config

    def _load(self, config: Path):
        with mock.patch("app.main.settings.FORECAST_POLICY_CONFIG_PATH", str(config)):
            return _load_forecast_policy_config()

    def test_a_missing_config_fails_closed(self) -> None:
        self.assertIsNone(self._load(self.root / "absent.json"))

    def test_a_missing_sidecar_fails_closed(self) -> None:
        config = self.root / "policy.json"
        config.write_text(json.dumps(self.payload), encoding="utf-8")
        self.assertIsNone(self._load(config))

    def test_a_wrong_sidecar_hash_fails_closed(self) -> None:
        config = self._write(self.payload, sidecar="0" * 64)
        self.assertIsNone(self._load(config))

    def test_a_sidecar_with_a_second_line_fails_closed(self) -> None:
        config = self.root / "policy.json"
        config.write_text(json.dumps(self.payload), encoding="utf-8")
        digest = hashlib.sha256(canonical_bytes(self.payload)).hexdigest()
        Path(str(config) + ".sha256").write_text(f"{digest}\nextra", encoding="utf-8")
        self.assertIsNone(self._load(config))

    def test_an_uppercase_sidecar_fails_closed(self) -> None:
        digest = hashlib.sha256(canonical_bytes(self.payload)).hexdigest().upper()
        config = self._write(self.payload, sidecar=digest)
        self.assertIsNone(self._load(config))

    def test_a_tampered_config_fails_closed(self) -> None:
        config = self._write(self.payload)
        # Flip a bound artifact ID without touching the sidecar.
        payload = json.loads(config.read_text(encoding="utf-8"))
        network = next(iter(payload["networks"]))
        payload["networks"][network]["forecast_rule_ids"][
            "flood_forecast"
        ] = "66666666-6666-4666-8666-666666666666"
        config.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.assertIsNone(self._load(config))

    def test_a_duplicate_key_fails_closed(self) -> None:
        config = self.root / "policy.json"
        raw = json.dumps(self.payload)
        # Splice a duplicate "schema_version" key into the raw text.
        config.write_text(raw[:-1] + ', "schema_version": 1}', encoding="utf-8")
        digest = hashlib.sha256(canonical_bytes(self.payload)).hexdigest()
        Path(str(config) + ".sha256").write_text(digest, encoding="utf-8")
        self.assertIsNone(self._load(config))

    def test_an_extra_top_level_key_fails_closed(self) -> None:
        payload = dict(self.payload)
        payload["content_sha256"] = "a" * 64
        self.assertIsNone(self._load(self._write(payload)))

    def test_a_wrong_schema_version_fails_closed(self) -> None:
        payload = dict(self.payload)
        payload["schema_version"] = 2
        self.assertIsNone(self._load(self._write(payload)))

    def test_a_non_uuid_network_key_fails_closed(self) -> None:
        payload = dict(self.payload)
        payload["networks"] = {
            "not-a-uuid": next(iter(self.payload["networks"].values()))
        }
        self.assertIsNone(self._load(self._write(payload)))

    def test_an_incomplete_selection_fails_closed(self) -> None:
        network_id = next(iter(self.payload["networks"]))
        entry = self.payload["networks"][network_id]
        payload = {
            "schema_version": 1,
            "networks": {
                network_id: {
                    "forecast_rule_ids": entry["forecast_rule_ids"],
                    "probability_calibration_ids": {
                        "flood_forecast": entry["probability_calibration_ids"][
                            "flood_forecast"
                        ]
                    },
                }
            },
        }
        # pipe_surcharge is missing from the selection.
        self.assertIsNone(self._load(self._write(payload)))

    def test_a_zero_artifact_id_fails_closed(self) -> None:
        payload = json.loads(json.dumps(self.payload))
        network_id = next(iter(payload["networks"]))
        payload["networks"][network_id]["forecast_rule_ids"][
            "flood_forecast"
        ] = "00000000-0000-0000-0000-000000000000"
        self.assertIsNone(self._load(self._write(payload)))

    def test_a_relative_artifact_path_is_rejected_by_the_artifact_validator(
        self,
    ) -> None:
        # The closed config stores IDs, not paths, so there is no way to smuggle
        # a relative path through it; the artifact root check lives in
        # _validate_forecast_artifacts and is exercised there.
        payload = json.loads(json.dumps(self.payload))
        network_id = next(iter(payload["networks"]))
        payload["networks"][network_id]["forecast_rule_ids"][
            "flood_forecast"
        ] = "../../etc/passwd"
        self.assertIsNone(self._load(self._write(payload)))


class ShippedArtifactRegistryTests(unittest.TestCase):
    """The artifacts the policy config binds must exist and re-hash."""

    def setUp(self) -> None:
        registry_path = ARTIFACT_ROOT / "registry.json"
        self.assertTrue(registry_path.is_file(), f"missing {registry_path}")
        self.registry = json.loads(registry_path.read_text(encoding="utf-8"))

    def test_the_registry_covers_every_bound_artifact(self) -> None:
        payload = json.loads(POLICY_CONFIG.read_text(encoding="utf-8"))
        bound: set[str] = set()
        for entry in payload["networks"].values():
            for group in entry.values():
                bound.update(str(value) for value in group.values())
        registered = {entry["artifact_id"] for entry in self.registry["artifacts"]}
        self.assertEqual(bound, registered)

    def test_every_artifact_file_matches_its_sidecar(self) -> None:
        for entry in self.registry["artifacts"]:
            path = ARTIFACT_ROOT / entry["file"]
            self.assertTrue(path.is_file(), f"missing artifact file {path}")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(digest, entry["sha256"], f"{path.name} drifted")
            sidecar = path.with_suffix(path.suffix + ".sha256")
            self.assertTrue(sidecar.is_file(), f"missing sidecar for {path.name}")
            self.assertEqual(sidecar.read_text(encoding="utf-8").strip(), digest)

    def test_every_artifact_declares_the_schema_the_database_expects(self) -> None:
        # _validate_forecast_artifacts compares the file's schema_version with
        # the row's artifact_schema_version / rule_schema_version, so a drift
        # here would make the forecast component not ready at runtime.
        for entry in self.registry["artifacts"]:
            document = json.loads(
                (ARTIFACT_ROOT / entry["file"]).read_text(encoding="utf-8")
            )
            self.assertEqual(document["schema_version"], 1, entry["file"])

    def test_a_registry_entry_cannot_escape_the_controlled_root(self) -> None:
        # The runner resolves every entry against HYDRAULIC_ARTIFACT_ROOT and
        # drops anything outside it; assert the shipped registry stays inside.
        root = ARTIFACT_ROOT.resolve()
        for entry in self.registry["artifacts"]:
            resolved = (ARTIFACT_ROOT / entry["file"]).resolve()
            self.assertTrue(resolved == root or root in resolved.parents, entry["file"])

    def test_copying_the_tree_keeps_the_policy_loadable(self) -> None:
        # The containers mount this tree read-only at /opt/scn/hydraulic, so a
        # copy to a different root must behave identically.
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "hydraulic"
            shutil.copytree(REPO / "docker" / "hydraulic", copy)
            with mock.patch(
                "app.main.settings.FORECAST_POLICY_CONFIG_PATH",
                str(copy / "forecast-policy" / "forecast-policy.json"),
            ):
                self.assertIsNotNone(_load_forecast_policy_config())


if __name__ == "__main__":
    unittest.main()
