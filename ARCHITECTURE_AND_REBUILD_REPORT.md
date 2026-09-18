# GridWise LLM: Technical Architecture

This document describes the architecture, invariants, and design decisions of the GridWise Smart Campus Energy Optimization backend for the BUP CSE FEST 2026 Preliminary.

---

## System Overview

```
POST /optimize-energy
       │
       ▼
┌─────────────────┐
│  Pydantic Schema │  ← strict input validation (extra="forbid")
│   Validation     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ LLM Interpreter  │  ← single Gemini call, structured output + schema
│ (llm_interpreter)│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Guardrails      │  ← deterministic validation of LLM output
│  (guardrails)    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  MILP Optimizer  │  ← SciPy HiGHS, 168 variables (7 per hour)
│  (optimizer)     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Replay Verifier │  ← independent constraint replay
│  (verifier)      │
└────────┬────────┘
         │
         ▼
   OptimizeResponse
```

---

## Component Responsibilities

### `app/schemas.py` — API Contract
- Pydantic models transcribed field-for-field from the official spec.
- `extra="forbid"` on all models rejects unknown fields.
- Strict type validators reject boolean-as-int, string-as-number coercion.

### `app/llm_interpreter.py` — Note Interpretation
- Sends all notes in a single Gemini request to minimize latency.
- Uses `response_schema` for structured JSON output constraining `directive_type` to valid enum values.
- Module-level client singleton (no per-call instantiation overhead).
- Retry policy covers transient HTTP errors (408, 429, 500, 502, 503, 504, timeouts, connection errors).
- Falls back to a lighter model if the primary model fails.
- Never silently converts a failed interpretation to `no_op`.

### `app/guardrails.py` — Deterministic Validation
- Validates every LLM output against spec rules before it reaches the optimizer.
- Enforces exact key sets per directive type (no extra keys allowed).
- Validates hour ranges (0–23, ascending, unique), numeric bounds, and type constraints.
- Raises `DirectiveValidationError` — never repairs or silently mutates.

### `app/optimizer.py` — MILP Solver
- 24-hour Mixed-Integer Linear Program using SciPy's HiGHS backend.
- 168 decision variables: 7 per hour (grid, solar, charge, discharge, SoC, is_charging binary, is_discharging binary).
- Objective: `min Σ tariff[h] × grid[h]` — nothing else in the objective.
- `mip_rel_gap = 0.0` for exact optimality.
- Tight grid bounds (`demand[h] + max_charge`) instead of arbitrary large values.
- Binary mutual exclusion constraint prevents simultaneous charge/discharge.

### `app/verifier.py` — Schedule Replay
- Independently replays the proposed schedule against all constraints.
- Tolerance: 0.01 kWh / BDT (matches official spec).
- Checks: energy balance, battery transitions, SoC bounds, directive compliance, end-of-day neutrality, total consistency.

### `app/main.py` — Orchestrator
- Wires components: interpret → optimize → verify → respond.
- Totals computed once from the verified hourly plan.
- `RequestValidationError` → 400; `RuntimeError` → 500 (sanitized, no internal details leaked).

---

## Key Invariants

1. **Objective purity**: The MILP objective contains only `tariff[h] × grid[h]` terms. No secondary penalties.
2. **Battery mutual exclusion**: `is_charging[h] + is_discharging[h] ≤ 1` enforced as a hard MILP constraint.
3. **End-of-day neutrality**: `SoC[23] = initial_energy` as an equality constraint.
4. **Directive key strictness**: `structured_adjustment` contains exactly the keys required for each directive type.
5. **Tolerance contract**: All comparisons use 0.01 absolute tolerance.
6. **No silent fallback**: LLM failure raises an error; the system never invents directives.

---

## Failure Modes and Handling

| Failure | Behavior |
|---|---|
| Invalid request body | 400 with structured Pydantic error details |
| LLM timeout (>25s) | Retry with fallback model, then 500 |
| LLM rate limit (429) | Exponential backoff retry (up to 3 attempts) |
| LLM returns invalid JSON | 500 (never invents no_op) |
| Guardrail rejection | 500 (never applies invalid directive) |
| MILP infeasible | 500 with solver error |
| Verifier replay fails | 500 (never returns unverified schedule) |

---

## Directive Composition Rules

| Directive Type | Multiple Overlap | Composition |
|---|---|---|
| solar_reduction | Same hour, different factors | Most restrictive (min of `original × factor`) |
| minimum_battery_reserve | Same hour, different values | Most restrictive (max of reserve values) |
| max_grid_window | Same hour, different caps | Most restrictive (min of caps) |
| no_charge_window | Multiple windows | Union of hours |
| no_discharge_window | Multiple windows | Union of hours |

---

## Known Limitations

1. Solar reduction composition uses `min(current, original × factor)` — factors do not compound multiplicatively. This is a design choice; the official spec does not fully specify multi-directive composition.
2. The fallback model may produce lower-quality interpretations than the primary model.
3. The 10-second MILP time limit may be insufficient for adversarial edge cases, though typical 24-hour problems solve in <1s.
4. Live LLM tests require API credentials and are not part of the default test suite.
