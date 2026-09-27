"""Make acceptance JSON fixtures directly collectable by pytest.

The collector validates only fixture syntax/schema.  It never interprets a
fixture as AC execution evidence or changes the matrix status.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ACCEPTANCE_ROOT = Path(__file__).resolve().parent


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


class AcceptanceJsonItem(pytest.Item):
    fixture_path: Path

    def runtest(self) -> None:
        payload: Any = json.loads(
            self.fixture_path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
        if isinstance(payload, dict):
            assert isinstance(payload.get("schema"), str)
        else:
            assert isinstance(payload, list)
            assert payload
            assert all(isinstance(item, dict) for item in payload)


class AcceptanceJsonFile(pytest.File):
    def collect(self):
        item = AcceptanceJsonItem.from_parent(
            self,
            name="test_acceptance_fixture_is_finite_and_schema_bearing",
        )
        item.fixture_path = self.path
        yield item


def pytest_collect_file(file_path: Path, path, parent: pytest.Collector):
    del path
    if file_path.suffix == ".json" and file_path.parent == ACCEPTANCE_ROOT:
        return AcceptanceJsonFile.from_parent(parent, path=file_path)
    return None
