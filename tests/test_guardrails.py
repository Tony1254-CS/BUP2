"""
Unit tests for ``app.guardrails.validate_directive``.

Tests that valid directives pass and invalid ones raise
``DirectiveValidationError`` with a descriptive message.
"""
import pytest

from app.guardrails import DirectiveValidationError, validate_directive
from app.schemas import BatteryInput

BAT = BatteryInput(
    capacity_kwh=500,
    initial_energy_kwh=250,
    minimum_energy_kwh=50,
    max_charge_kwh_per_hour=100,
    max_discharge_kwh_per_hour=100,
)


# ── Valid directives ──────────────────────────────────────────────────────

class TestValidDirectives:
    def test_valid_no_op(self):
        d = {"applies": False, "directive_type": "no_op",
             "structured_adjustment": None, "explanation": "ok"}
        validate_directive(d, BAT)  # should not raise

    def test_valid_solar_reduction(self):
        d = {"applies": True, "directive_type": "solar_reduction",
             "structured_adjustment": {"hours": [10, 11, 12], "factor": 0.2},
             "explanation": "ok"}
        validate_directive(d, BAT)

    def test_valid_no_charge_window(self):
        d = {"applies": True, "directive_type": "no_charge_window",
             "structured_adjustment": {"hours": [18, 19, 20]},
             "explanation": "ok"}
        validate_directive(d, BAT)

    def test_valid_no_discharge_window(self):
        d = {"applies": True, "directive_type": "no_discharge_window",
             "structured_adjustment": {"hours": [0, 1, 2, 3]},
             "explanation": "ok"}
        validate_directive(d, BAT)

    def test_valid_min_battery_reserve(self):
        d = {"applies": True, "directive_type": "minimum_battery_reserve",
             "structured_adjustment": {"hours": list(range(24)),
                                       "minimum_energy_kwh": 200},
             "explanation": "ok"}
        validate_directive(d, BAT)

    def test_valid_max_grid_window(self):
        d = {"applies": True, "directive_type": "max_grid_window",
             "structured_adjustment": {"hours": [13, 14], "max_grid_kwh": 300},
             "explanation": "ok"}
        validate_directive(d, BAT)


# ── Invalid directives ───────────────────────────────────────────────────

class TestInvalidDirectives:
    def test_unknown_type(self):
        d = {"applies": True, "directive_type": "magic_spell",
             "structured_adjustment": {}, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="Unknown directive_type"):
            validate_directive(d, BAT)

    def test_no_op_with_applies_true(self):
        d = {"applies": True, "directive_type": "no_op",
             "structured_adjustment": None, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="applies=false"):
            validate_directive(d, BAT)

    def test_no_op_with_adjustment(self):
        d = {"applies": False, "directive_type": "no_op",
             "structured_adjustment": {"hours": [1]}, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="null"):
            validate_directive(d, BAT)

    def test_non_noop_applies_false(self):
        d = {"applies": False, "directive_type": "solar_reduction",
             "structured_adjustment": {"hours": [1], "factor": 0.5},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="applies=true"):
            validate_directive(d, BAT)

    def test_empty_hours(self):
        d = {"applies": True, "directive_type": "no_charge_window",
             "structured_adjustment": {"hours": []}, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="non-empty"):
            validate_directive(d, BAT)

    def test_hours_not_ascending(self):
        d = {"applies": True, "directive_type": "no_charge_window",
             "structured_adjustment": {"hours": [5, 3, 7]}, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="ascending"):
            validate_directive(d, BAT)

    def test_hours_out_of_range(self):
        d = {"applies": True, "directive_type": "no_charge_window",
             "structured_adjustment": {"hours": [25]}, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="out of range"):
            validate_directive(d, BAT)

    def test_duplicate_hours(self):
        d = {"applies": True, "directive_type": "no_charge_window",
             "structured_adjustment": {"hours": [5, 5, 6]}, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="ascending"):
            validate_directive(d, BAT)

    def test_solar_factor_out_of_range(self):
        d = {"applies": True, "directive_type": "solar_reduction",
             "structured_adjustment": {"hours": [10], "factor": 1.5},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="\\[0, 1\\]"):
            validate_directive(d, BAT)

    def test_solar_factor_must_be_finite(self):
        d = {"applies": True, "directive_type": "solar_reduction",
             "structured_adjustment": {"hours": [10], "factor": float("inf")},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="\\[0, 1\\]"):
            validate_directive(d, BAT)

    def test_reserve_cannot_exceed_capacity(self):
        d = {"applies": True, "directive_type": "minimum_battery_reserve",
             "structured_adjustment": {"hours": [10], "minimum_energy_kwh": 501},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="capacity"):
            validate_directive(d, BAT)

    def test_boolean_hour_is_rejected(self):
        d = {"applies": True, "directive_type": "no_charge_window",
             "structured_adjustment": {"hours": [True]}, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="must be int"):
            validate_directive(d, BAT)

    def test_solar_missing_factor(self):
        d = {"applies": True, "directive_type": "solar_reduction",
             "structured_adjustment": {"hours": [10]}, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="factor"):
            validate_directive(d, BAT)

    def test_max_grid_negative(self):
        d = {"applies": True, "directive_type": "max_grid_window",
             "structured_adjustment": {"hours": [13], "max_grid_kwh": -10},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match=">= 0"):
            validate_directive(d, BAT)

    def test_missing_structured_adjustment(self):
        d = {"applies": True, "directive_type": "no_charge_window",
             "structured_adjustment": None, "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="dict"):
            validate_directive(d, BAT)


# ── Extra-key rejection (Issue #6) ───────────────────────────────────────

class TestExtraKeyRejection:
    """structured_adjustment must contain EXACTLY the required keys."""

    def test_solar_extra_key_rejected(self):
        d = {"applies": True, "directive_type": "solar_reduction",
             "structured_adjustment": {"hours": [10], "factor": 0.2, "max_grid_kwh": 99999},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="exactly"):
            validate_directive(d, BAT)

    def test_no_charge_extra_key_rejected(self):
        d = {"applies": True, "directive_type": "no_charge_window",
             "structured_adjustment": {"hours": [10], "factor": 0.5},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="exactly"):
            validate_directive(d, BAT)

    def test_reserve_extra_key_rejected(self):
        d = {"applies": True, "directive_type": "minimum_battery_reserve",
             "structured_adjustment": {"hours": [10], "minimum_energy_kwh": 100, "extra": 1},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="exactly"):
            validate_directive(d, BAT)

    def test_max_grid_extra_key_rejected(self):
        d = {"applies": True, "directive_type": "max_grid_window",
             "structured_adjustment": {"hours": [13], "max_grid_kwh": 300, "factor": 0.5},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="exactly"):
            validate_directive(d, BAT)

    def test_no_discharge_extra_key_rejected(self):
        d = {"applies": True, "directive_type": "no_discharge_window",
             "structured_adjustment": {"hours": [0, 1], "minimum_energy_kwh": 100},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError, match="exactly"):
            validate_directive(d, BAT)

    def test_solar_missing_key_rejected(self):
        """Missing required key should also fail exact-key check."""
        d = {"applies": True, "directive_type": "solar_reduction",
             "structured_adjustment": {"hours": [10]},
             "explanation": "x"}
        with pytest.raises(DirectiveValidationError):
            validate_directive(d, BAT)
