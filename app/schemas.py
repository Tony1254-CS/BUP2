"""
Pydantic models transcribed field-for-field from the official GridWise spec.

Source of truth: _meta.schema_notes and _meta.allowed_enums from the
official sample case pack, plus the problem statement sections 07 and 10.

Assumptions flagged:
  - All numeric energy/cost fields are float (spec uses kWh/BDT values with
    decimals like 2692.5).
  - operator_notes min_length=1, max_length=3 per spec "1-3 non-empty
    natural-language strings".
  - hours and hourly_plan must each contain exactly 24 entries (spec:
    "exactly 24 unique entries for hours 0 through 23").
"""
from __future__ import annotations

import math
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ── Input schema ──────────────────────────────────────────────────────────

class HourInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0)
    solar_kwh: float = Field(..., ge=0)
    tariff_bdt_per_kwh: float = Field(..., ge=0)

    @field_validator("hour", mode="before")
    @classmethod
    def strict_hour(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("hour must be an integer")
        return value

    @field_validator(
        "demand_kwh",
        "solar_kwh",
        "tariff_bdt_per_kwh",
        mode="before",
    )
    @classmethod
    def strict_numeric_inputs(cls, value: Any) -> Any:
        if isinstance(value, bool) or isinstance(value, str):
            raise ValueError("numeric fields must be JSON numbers")
        return value

    @field_validator("demand_kwh", "solar_kwh", "tariff_bdt_per_kwh")
    @classmethod
    def finite_values(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return value


class BatteryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capacity_kwh: float = Field(..., gt=0)
    initial_energy_kwh: float = Field(..., ge=0)
    minimum_energy_kwh: float = Field(..., ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)

    @field_validator(
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
        mode="before",
    )
    @classmethod
    def strict_numeric_inputs(cls, value: Any) -> Any:
        if isinstance(value, bool) or isinstance(value, str):
            raise ValueError("numeric fields must be JSON numbers")
        return value

    @field_validator(
        "capacity_kwh",
        "initial_energy_kwh",
        "minimum_energy_kwh",
        "max_charge_kwh_per_hour",
        "max_discharge_kwh_per_hour",
    )
    @classmethod
    def finite_values(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("value must be finite")
        return value

    @model_validator(mode="after")
    def validate_relationships(self) -> "BatteryInput":
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh must not exceed capacity_kwh")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh must not exceed capacity_kwh")
        if self.minimum_energy_kwh > self.initial_energy_kwh:
            raise ValueError(
                "minimum_energy_kwh must not exceed initial_energy_kwh"
            )
        return self


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    operator_notes: list[str] = Field(..., min_length=1, max_length=3)
    hours: list[HourInput] = Field(..., min_length=24, max_length=24)
    battery: BatteryInput

    @field_validator("scenario_id")
    @classmethod
    def non_empty_scenario_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("scenario_id must be non-empty")
        return value

    @field_validator("operator_notes")
    @classmethod
    def non_empty_operator_notes(cls, value: list[str]) -> list[str]:
        if any(not note.strip() for note in value):
            raise ValueError("operator_notes must contain non-empty strings")
        return value

    @model_validator(mode="after")
    def validate_hours(self) -> "OptimizeRequest":
        hour_ids = [entry.hour for entry in self.hours]
        if set(hour_ids) != set(range(24)):
            raise ValueError("hours must contain each hour from 0 through 23 exactly once")
        return self


# ── Allowed enums (verbatim from spec) ────────────────────────────────────

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]

BatteryAction = Literal["charge", "discharge", "idle"]


# ── Output schema ─────────────────────────────────────────────────────────

class DirectiveInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_index: int = Field(..., ge=0)
    applies: bool
    directive_type: DirectiveType
    structured_adjustment: Optional[dict[str, Any]] = None
    explanation: str


class HourlyPlanEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float = Field(..., ge=0)
    solar_used_kwh: float = Field(..., ge=0)
    battery_action: BatteryAction
    battery_kwh: float = Field(..., ge=0)
    battery_energy_after_kwh: float = Field(..., ge=0)


class OptimizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = "ok"
