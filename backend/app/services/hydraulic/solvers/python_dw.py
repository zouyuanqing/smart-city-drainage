"""``py_dw``: the pure-Python diffusion-wave solver identity.

``py_dw`` is the second frozen pure-Python identity
(``IMPLEMENTATION_CONTRACT.md`` §5.5 and the canonical mapping in §0.4).  It
routes a conservative flux with the local inertial term omitted, and it is
strictly weaker than the EPA dynamic-wave engine in every respect that matters:

* it only accepts a mild-slope, subcritical, open-channel regime;
* a Froude number ``>= 0.95``, a rapid flow-direction reversal, or any
  pressure-flow condition is a **terminal refusal**, not a warning;
* it declares ``model_fidelity="reduced_fidelity"`` and
  ``authoritative=False`` and MUST NOT be described as ``swmm_dw``.

Those three properties are the whole point of this module.  Sharing the
machinery with :mod:`app.services.hydraulic.solvers.python_mw` guarantees the
two identities publish an identical frame schema, unit set, and progress bands
(§5.5: "所有 solver 输出相同 frame schema、unit、solver/version、quality
diagnostics"), while the routing kernel and the regime guard stay separate.

Static screening happens in :meth:`PythonDiffusionWaveSolver.validate` before a
simulation is queued; the dynamic gate runs inside the kernel and surfaces as
``UNSUPPORTED_FEATURE`` through the shared error mapping.
"""

from __future__ import annotations

import math
from typing import Any

from app.services.hydraulic.solvers.base import ValidationIssue
from app.services.hydraulic.solvers.python_hydraulics import (
    HydraulicNetwork,
    RoutingMode,
)
from app.services.hydraulic.solvers.python_mw import PythonRoutingSolver

#: The steepest invert gradient ``py_dw`` will accept on a single pipe.
#: ``swmm_dw`` models mild-slope networks; a pipe steeper than this is outside
#: the reduced-fidelity identity's declared domain and is refused up front.
MAX_ACCEPTABLE_PIPE_SLOPE = 0.02

#: A pipe whose full-bore capacity is below this is the regime where the
#: reduced-fidelity relation is most sensitive; ``py_dw`` still solves it, but
#: records the fact instead of hiding it.
SMALL_PIPE_DIAMETER_M = 0.3


class PythonDiffusionWaveSolver(PythonRoutingSolver):
    """``py_dw``: conservative diffusion-wave routing with a hard regime gate."""

    solver_id = "py_dw"
    version = "py_dw/1.0.0"
    routing_mode = RoutingMode.DIFFUSION_WAVE
    model_fidelity = "reduced_fidelity"
    routing_modes = ("diffusion_wave",)
    supported_features = frozenset(
        {
            "gravity",
            "routing",
            "looped_networks",
            "conservative_flux",
            "subcritical_open_channel",
        }
    )
    #: A network that demands the local inertial term, pressurised flow, or a
    #: supercritical regime is refused; it must not be quietly routed by a
    #: weaker identity.
    rejected_features = frozenset(
        {
            "dynamic_wave",
            "local_inertial",
            "pressure_flow",
            "supercritical_flow",
            "ponding",
            "water_hammer",
        }
    )

    def _regime_issues(
        self, network: HydraulicNetwork, snapshot: Any
    ) -> list[ValidationIssue]:
        """Screen the static geometry against the declared domain."""

        issues: list[ValidationIssue] = []
        for pipe in network.pipes:
            if pipe.slope > MAX_ACCEPTABLE_PIPE_SLOPE:
                issues.append(
                    ValidationIssue(
                        code="DW_SLOPE_OUT_OF_DOMAIN",
                        field="network.pipes",
                        message=(
                            f"pipe {pipe.pipe_id!r} has slope {pipe.slope:.6g} "
                            f"> {MAX_ACCEPTABLE_PIPE_SLOPE}; py_dw only accepts "
                            "mild-slope networks"
                        ),
                        object_ids=(pipe.pipe_id,),
                        details={"slope": pipe.slope},
                    )
                )
            if pipe.shape.strip().lower() == "circular":
                diameter = pipe.diameter_m
                if (
                    diameter is not None
                    and diameter < SMALL_PIPE_DIAMETER_M
                    and not math.isclose(diameter, SMALL_PIPE_DIAMETER_M)
                ):
                    issues.append(
                        ValidationIssue(
                            code="DW_SMALL_PIPE_SENSITIVITY",
                            field="network.pipes",
                            message=(
                                f"pipe {pipe.pipe_id!r} diameter {diameter} m is "
                                f"below {SMALL_PIPE_DIAMETER_M} m; the "
                                "reduced-fidelity relation is highly sensitive"
                            ),
                            object_ids=(pipe.pipe_id,),
                            details={"diameter_m": diameter},
                        )
                    )
        return issues

    def capabilities_override(self) -> dict[str, Any]:
        """Machine-readable statement of what this identity will never do."""

        return {
            "authoritative": False,
            "model_fidelity": self.model_fidelity,
            "rejected_features": sorted(self.rejected_features),
            "regime_gate": {
                "froude_max": 0.95,
                "pressure_flow": "rejected",
                "rapid_reversal": "rejected",
                "max_acceptable_pipe_slope": MAX_ACCEPTABLE_PIPE_SLOPE,
            },
            "must_not_be_described_as": "swmm_dw",
        }


def make_diffusion_wave_solver(
    version: str | None = None,
) -> PythonDiffusionWaveSolver:
    """Build a fresh ``py_dw`` instance; the registry owns long-lived copies."""

    return PythonDiffusionWaveSolver(version=version)


__all__ = [
    "MAX_ACCEPTABLE_PIPE_SLOPE",
    "SMALL_PIPE_DIAMETER_M",
    "PythonDiffusionWaveSolver",
    "make_diffusion_wave_solver",
]
