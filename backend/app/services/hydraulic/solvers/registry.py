"""The default solver registry: pure-Python always, EPA SWMM only when proven.

``IMPLEMENTATION_CONTRACT.md`` §5.2 fixes the selection semantics and §15.3
gives this file its single ownership rule: **it must never allow a silent
fallback during a run.**  This module therefore does exactly two things:

1. it registers the always-available pure-Python identities (``py_mw``,
   ``py_dw``) so a Windows host with no EPA binary can still solve;
2. it *optionally* registers the EPA SWMM identities, and only after a real
   probe of the configured executable's path, version, and SHA-256 has
   succeeded.  Registration is decided once, at build time, and the resulting
   registry is immutable for the lifetime of a simulation.

Selection itself is delegated to
:class:`~app.services.hydraulic.solvers.base.SolverRegistry`, which already
implements the frozen rules:

* ``explicit`` requests exactly one solver and raises
  ``SOLVER_UNAVAILABLE`` when the probe fails — never a downgrade;
* ``auto`` probes the requested solver first, then ``py_mw``;
* ``auto`` with no request prefers ``swmm_kw``, then ``py_mw``.

A *run-time* failure is a different matter: it propagates.  Nothing in this
module, or anywhere else on the run path, catches a solver error and restarts
it under a different identity.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from app.services.hydraulic.solvers.base import (
    DEFAULT_FALLBACK_SOLVER_ID,
    SOLVER_IDS,
    SWMM_SOLVER_IDS,
    SolverDegradationReport,
    SolverRegistry,
    SolverSelection,
    SolverSelectionResult,
    create_solver_registry,
)
from app.services.hydraulic.solvers.python_dw import PythonDiffusionWaveSolver
from app.services.hydraulic.solvers.python_mw import PythonMotionWaveSolver

#: The two pure-Python identities this module is responsible for registering.
#: ``py_ponding`` is owned by the surface-flood component and is deliberately
#: *not* registered here: pretending a 1D ponding surrogate is a 2D surface
#: solver would violate §5.5.
PYTHON_FALLBACK_SOLVERS: tuple[str, ...] = ("py_mw", "py_dw")

#: The EPA identities that share one pinned engine binary.
EPA_SOLVER_IDS: tuple[str, ...] = ("swmm_kw", "swmm_dw")

_LOCK = threading.Lock()
_DEFAULT_REGISTRY: SolverRegistry | None = None


def build_python_solvers() -> tuple[Any, ...]:
    """Fresh instances of the pure-Python identities."""

    return (PythonMotionWaveSolver(), PythonDiffusionWaveSolver())


def probe_epa_swmm(settings: Any) -> dict[str, Any]:
    """Report whether the optional EPA engine is configured *and* usable.

    The three EPA settings must be present together.  This function performs no
    import of a native library and starts no process: it is exactly the
    configured-vs-available distinction the contract requires, and it never
    raises.
    """

    try:
        report = dict(settings.probe_epa_swmm())
    except Exception as exc:  # pragma: no cover - a broken settings object
        return {
            "configured": False,
            "available": False,
            "reason": f"probe_failed:{type(exc).__name__}",
        }
    report.setdefault("configured", False)
    report.setdefault("available", False)
    report.setdefault("reason", "not_configured")
    return report


def build_epa_solvers(settings: Any) -> tuple[Any, ...]:
    """Build the EPA adapters, or nothing when the engine is not available.

    An unconfigured or hash-mismatched engine yields an empty tuple.  That is
    the honest "no EPA here" state; the registry then offers ``py_mw`` and the
    selection report carries ``not_configured`` / ``sha256_mismatch`` as the
    recorded fallback reason.
    """

    report = probe_epa_swmm(settings)
    if not report.get("available"):
        return ()
    from app.services.hydraulic.solvers.swmm_adapter import (
        Swmm5OutputParser,
        SwmmAdapter,
    )

    version = str(report.get("version") or "")
    try:
        parser = Swmm5OutputParser(version)
    except ValueError:
        # A pinned engine version this parser release does not support is an
        # honest unavailability, not a reason to guess a layout.
        return ()
    return tuple(
        SwmmAdapter.from_settings(settings, solver_id=solver_id, output_parser=parser)
        for solver_id in EPA_SOLVER_IDS
    )


def build_default_registry(
    *,
    settings: Any = None,
    include_epa: bool = True,
    fallback_solver_id: str = DEFAULT_FALLBACK_SOLVER_ID,
) -> SolverRegistry:
    """Assemble a registry from the pure-Python identities plus optional EPA.

    ``settings`` is any object exposing the three ``EPA_SWMM_*`` attributes and
    ``probe_epa_swmm()`` (the application :class:`app.core.config.Settings`
    satisfies this).  Passing ``settings=None`` builds the pure-Python-only
    registry, which is what a test, a CLI ``--list-solvers``, or a host with no
    EPA engine gets.
    """

    if fallback_solver_id not in PYTHON_FALLBACK_SOLVERS:
        raise ValueError(
            f"fallback_solver_id must be one of {PYTHON_FALLBACK_SOLVERS}, got "
            f"{fallback_solver_id!r}"
        )
    solvers: list[Any] = list(build_python_solvers())
    if include_epa and settings is not None:
        solvers.extend(build_epa_solvers(settings))
    return create_solver_registry(solvers, fallback_solver_id=fallback_solver_id)


def settings_from_environ(environ: Any = None) -> Any | None:
    """Load the application settings, or ``None`` when they are unavailable.

    The registry must not make importing the application configuration a
    precondition: a standalone ``python -m ...cli --list-solvers`` on a machine
    without a database still has to be able to report the pure-Python
    solvers.  Any failure to import or to construct settings degrades to "no
    EPA configured" rather than to an exception.
    """

    env = os.environ if environ is None else environ
    try:
        from app.core.config import Settings

        return Settings()
    except Exception:
        # ``Settings()`` reads the process environment itself; a missing or
        # partially configured deployment simply means EPA is not usable here.
        del env
        return None


def get_default_registry(
    *,
    settings: Any = None,
    include_epa: bool = True,
    refresh: bool = False,
) -> SolverRegistry:
    """Return the process-wide registry, building it on first use.

    The registry is cached because selection happens inside request handlers;
    the cache is never mutated afterwards, so a frozen selection stays valid
    for the lifetime of a simulation.
    """

    global _DEFAULT_REGISTRY
    with _LOCK:
        if _DEFAULT_REGISTRY is not None and not refresh and settings is None:
            return _DEFAULT_REGISTRY
        resolved = settings if settings is not None else settings_from_environ()
        registry = build_default_registry(settings=resolved, include_epa=include_epa)
        if settings is None:
            _DEFAULT_REGISTRY = registry
        return registry


def reset_default_registry() -> None:
    """Drop the cached registry; the next call rebuilds and re-probes EPA."""

    global _DEFAULT_REGISTRY
    with _LOCK:
        _DEFAULT_REGISTRY = None


def registered_solver_ids(registry: SolverRegistry | None = None) -> tuple[str, ...]:
    active = registry if registry is not None else get_default_registry()
    return active.solver_ids


def supported_solver_ids() -> tuple[str, ...]:
    """Every identity the frozen contract defines, registered or not."""

    return SOLVER_IDS


def unregistered_solver_ids(
    registry: SolverRegistry | None = None,
) -> tuple[str, ...]:
    """Identities this deployment cannot currently run, with the reason left
    to the caller to state honestly rather than to guess here."""

    active = registry if registry is not None else get_default_registry()
    return tuple(solver_id for solver_id in SOLVER_IDS if solver_id not in active)


async def select_solver(
    selection: str | SolverSelection = SolverSelection.auto,
    requested_solver_id: str | None = None,
    *,
    registry: SolverRegistry | None = None,
) -> SolverSelectionResult:
    """Freeze a solver identity at submission time.

    The returned ``SolverSelectionResult`` is the durable fact written to
    ``simulations.solver_id``; nothing downstream is allowed to change it.
    """

    active = registry if registry is not None else get_default_registry()
    return await active.select(selection, requested_solver_id)


async def selection_report(
    selection: str | SolverSelection = SolverSelection.auto,
    requested_solver_id: str | None = None,
    *,
    registry: SolverRegistry | None = None,
) -> SolverDegradationReport:
    """The auditable probe/fallback record for a selection decision."""

    active = registry if registry is not None else get_default_registry()
    return await active.capability_report(selection, requested_solver_id)


def get_solver(solver_id: str, *, registry: SolverRegistry | None = None) -> Any:
    """Return a registered solver instance, or raise ``SOLVER_UNAVAILABLE``."""

    active = registry if registry is not None else get_default_registry()
    return active.get(solver_id)


def is_available(solver_id: str, *, registry: SolverRegistry | None = None) -> bool:
    """Cheap synchronous membership check (registration, not the probe)."""

    active = registry if registry is not None else get_default_registry()
    return solver_id in active


def is_swmm_solver(solver_id: str) -> bool:
    return solver_id in SWMM_SOLVER_IDS


def epa_status(*, settings: Any = None) -> dict[str, Any]:
    """A single JSON-serialisable EPA fact record for diagnostics and CLI."""

    resolved = settings if settings is not None else settings_from_environ()
    if resolved is None:
        return {
            "configured": False,
            "available": False,
            "reason": "settings_unavailable",
            "solver_ids": list(EPA_SOLVER_IDS),
        }
    report = probe_epa_swmm(resolved)
    report["solver_ids"] = list(EPA_SOLVER_IDS)
    return report


def describe(registry: SolverRegistry | None = None) -> dict[str, Any]:
    """A full capability/degradation snapshot for the CLI and diagnostics."""

    active = registry if registry is not None else get_default_registry()
    return {
        "schema": "solver-registry-report-v1",
        "registered_solver_ids": list(active.solver_ids),
        "unregistered_solver_ids": list(unregistered_solver_ids(active)),
        "fallback_solver_id": active.fallback_solver_id,
        "epa": epa_status(),
        "capabilities": {
            solver_id: active.get(solver_id).capabilities().to_dict()
            for solver_id in active.solver_ids
        },
    }


__all__ = [
    "EPA_SOLVER_IDS",
    "PYTHON_FALLBACK_SOLVERS",
    "build_default_registry",
    "build_epa_solvers",
    "build_python_solvers",
    "describe",
    "epa_status",
    "get_default_registry",
    "get_solver",
    "is_available",
    "is_swmm_solver",
    "probe_epa_swmm",
    "registered_solver_ids",
    "reset_default_registry",
    "select_solver",
    "selection_report",
    "settings_from_environ",
    "supported_solver_ids",
    "unregistered_solver_ids",
]
