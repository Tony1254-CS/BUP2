"""
Pure-function guardrails for directive validation.

Every rule traces to an explicit line in the spec's interpretation_rules
or constraint_reminders.  NO I/O, NO imports beyond stdlib + schemas.

Raises ``DirectiveValidationError`` on any violation so the caller can
catch and fall back to no_op for that single note.
"""
from __future__ import annotations

import math
from typing import Any

from app.schemas import BatteryInput

VALID_DIRECTIVE_TYPES = frozenset(
    {
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    }
)


class DirectiveValidationError(Exception):
    """Raised when a raw directive dict violates spec rules."""


def validate_directive(raw: dict[str, Any], battery: BatteryInput) -> None:
    """
    Validate a single raw directive dict in-place.
    Raises ``DirectiveValidationError`` on any violation.
    Does NOT mutate or repair — caller must catch and fall back.

    Checked rules (spec references):
      - directive_type must be one of the 6 allowed enum values
      - For no_op: applies must be False AND structured_adjustment must be None/null
      - For every non-no_op: applies must be True
      - structured_adjustment must contain the required keys for the type
      - hours must be a non-empty list of unique ints 0..23 in ascending order
      - factor must be a float in [0, 1] for solar_reduction
      - minimum_energy_kwh must be > 0 for minimum_battery_reserve
      - max_grid_kwh must be >= 0 for max_grid_window
    """
    # ── directive_type ────────────────────────────────────────────────────
    dtype = raw.get("directive_type")
    if dtype not in VALID_DIRECTIVE_TYPES:
        raise DirectiveValidationError(
            f"Unknown directive_type: {dtype!r}. "
            f"Must be one of {sorted(VALID_DIRECTIVE_TYPES)}"
        )

    applies = raw.get("applies")
    adj = raw.get("structured_adjustment")

    # ── no_op rules ───────────────────────────────────────────────────────
    if dtype == "no_op":
        if applies is not False:
            raise DirectiveValidationError(
                "no_op directive must have applies=false"
            )
        if adj is not None:
            raise DirectiveValidationError(
                "no_op directive must have structured_adjustment=null"
            )
        return  # valid no_op

    # ── non-no_op must have applies=True ──────────────────────────────────
    if applies is not True:
        raise DirectiveValidationError(
            f"{dtype} directive must have applies=true, got {applies!r}"
        )

    # ── structured_adjustment must be a dict ──────────────────────────────
    if not isinstance(adj, dict):
        raise DirectiveValidationError(
            f"{dtype} requires a dict structured_adjustment, got {type(adj).__name__}"
        )

    # ── hours validation (all non-no_op types need hours) ─────────────────
    hours = adj.get("hours")
    _validate_hours(hours, dtype)

    # ── type-specific fields ──────────────────────────────────────────────
    if dtype == "solar_reduction":
        factor = adj.get("factor")
        if factor is None:
            raise DirectiveValidationError("solar_reduction requires 'factor'")
        if isinstance(factor, bool) or not isinstance(factor, (int, float)):
            raise DirectiveValidationError(
                f"solar_reduction factor must be numeric, got {type(factor).__name__}"
            )
        if not math.isfinite(float(factor)) or not (0.0 <= float(factor) <= 1.0):
            raise DirectiveValidationError(
                f"solar_reduction factor must be in [0, 1], got {factor}"
            )

    elif dtype == "minimum_battery_reserve":
        mek = adj.get("minimum_energy_kwh")
        if mek is None:
            raise DirectiveValidationError(
                "minimum_battery_reserve requires 'minimum_energy_kwh'"
            )
        if isinstance(mek, bool) or not isinstance(mek, (int, float)):
            raise DirectiveValidationError(
                "minimum_energy_kwh must be numeric"
            )
        if (
            not math.isfinite(float(mek))
            or float(mek) < 0
            or float(mek) > battery.capacity_kwh
        ):
            raise DirectiveValidationError(
                f"minimum_energy_kwh must be finite, >= 0, and <= battery capacity; got {mek}"
            )

    elif dtype == "max_grid_window":
        mgk = adj.get("max_grid_kwh")
        if mgk is None:
            raise DirectiveValidationError(
                "max_grid_window requires 'max_grid_kwh'"
            )
        if isinstance(mgk, bool) or not isinstance(mgk, (int, float)):
            raise DirectiveValidationError("max_grid_kwh must be numeric")
        if not math.isfinite(float(mgk)) or float(mgk) < 0:
            raise DirectiveValidationError(
                f"max_grid_kwh must be finite and >= 0, got {mgk}"
            )

    elif dtype in ("no_charge_window", "no_discharge_window"):
        # Only hours required — already validated above
        pass


def _validate_hours(hours: Any, dtype: str) -> None:
    """Validate the hours array per spec interpretation_rules."""
    if not isinstance(hours, list):
        raise DirectiveValidationError(
            f"{dtype}: 'hours' must be a list, got {type(hours).__name__}"
        )
    if len(hours) == 0:
        raise DirectiveValidationError(f"{dtype}: 'hours' must be non-empty")

    prev = -1
    for i, h in enumerate(hours):
        if isinstance(h, bool) or not isinstance(h, int):
            raise DirectiveValidationError(
                f"{dtype}: hours[{i}] must be int, got {type(h).__name__} ({h!r})"
            )
        if h < 0 or h > 23:
            raise DirectiveValidationError(
                f"{dtype}: hours[{i}]={h} out of range [0, 23]"
            )
        if h <= prev:
            raise DirectiveValidationError(
                f"{dtype}: hours must be unique and ascending; "
                f"hours[{i}]={h} is not > previous {prev}"
            )
        prev = h
