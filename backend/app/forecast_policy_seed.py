"""One-shot forecast policy seed: bind the shipped artifacts to database rows.

``FORECAST_POLICY_CONFIG_PATH`` names four immutable artifacts and the API
re-hashes every one of them at ``/readyz`` time
(``app/main.py::_validate_forecast_artifacts``).  That check has two halves:
the file on disk, and the ``hydraulic.probability_calibrations`` /
``hydraulic.forecast_rules`` rows that record the same URI and digest.  Alembic
creates those tables but never inserts into them, so without this step the
policy config references artifact IDs the database does not know and the
forecast component reports not ready forever.

The rows are data, not schema: this step creates no table, extension or
hypertable, so it does not compete with the single Alembic writer.  The SQL is
``ON CONFLICT DO UPDATE`` throughout, so re-running it converges the database
onto the shipped files instead of duplicating or failing.

A failure is fatal and rolls the whole transaction back.  Partially applied
bindings would let a policy artifact disagree with the digest the API enforces,
which is worse than not seeding at all.

Entry point: ``python -m app.forecast_policy_seed``.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

import psycopg2

DEFAULT_SEED_PATH = "/opt/scn/hydraulic/forecast-policy/seed-artifact-rows.sql"
BOUND_TABLES = (
    "hydraulic.probability_calibrations",
    "hydraulic.forecast_rules",
)

logger = logging.getLogger("app.forecast_policy_seed")


def resolve_dsn(environ: dict[str, str] | None = None) -> str:
    """Return a libpq DSN, preferring the sync URL over the async one.

    ``DATABASE_URL`` is written for asyncpg (``postgresql+asyncpg://``), which
    psycopg2 cannot parse, so the driver suffix is stripped when the sync URL
    is absent.
    """
    env = os.environ if environ is None else environ
    dsn = str(env.get("DATABASE_URL_SYNC") or "").strip()
    if not dsn:
        dsn = str(env.get("DATABASE_URL") or "").strip()
    if not dsn:
        raise RuntimeError("DATABASE_URL_SYNC or DATABASE_URL is required")
    for prefix in ("postgresql+asyncpg://", "postgres+asyncpg://"):
        if dsn.startswith(prefix):
            return "postgresql://" + dsn[len(prefix) :]
    return dsn


def read_script(path: Path) -> str:
    """Read the seed file, dropping psql meta-commands.

    The file is written for ``psql -f`` and opens with ``\\set ON_ERROR_STOP
    on``.  That directive is a psql client command, not SQL, and libpq would
    reject it as a syntax error, so it is dropped here.  Everything the
    statement level still has to enforce -- stop on the first error and roll
    the whole script back -- is provided by running every statement inside one
    transaction.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.lstrip().startswith("\\")]
    return "\n".join(kept) + "\n"


def apply_seed(dsn: str, script: str) -> dict[str, int]:
    """Apply the seed in one transaction and report the bound row counts."""
    counts: dict[str, int] = {}
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(script)
            for table in BOUND_TABLES:
                cursor.execute(f"SELECT count(*) FROM {table}")
                counts[table] = int(cursor.fetchone()[0])
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.forecast_policy_seed",
        description=(
            "Bind the shipped forecast policy artifacts to their database rows. "
            "Idempotent; exits non-zero on any failure."
        ),
    )
    parser.add_argument(
        "--seed",
        type=Path,
        default=Path(os.environ.get("FORECAST_POLICY_SEED_PATH", DEFAULT_SEED_PATH)),
        help="path to seed-artifact-rows.sql",
    )
    parser.add_argument("--log-level", default=None, help="Override LOG_LEVEL.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=str(args.log_level or os.environ.get("LOG_LEVEL") or "INFO"),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if not args.seed.is_file():
        logger.error("forecast policy seed file is missing: %s", args.seed)
        return 1
    try:
        counts = apply_seed(resolve_dsn(), read_script(args.seed))
    except Exception as exc:
        logger.error("forecast policy seed failed: %s", exc)
        return 1
    logger.info("forecast policy seed applied: %s", counts)
    if any(count <= 0 for count in counts.values()):
        logger.error("forecast policy seed left a bound table empty: %s", counts)
        return 1
    print(f"FORECAST_POLICY_SEED=OK {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
