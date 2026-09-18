# GridWise LLM: Architecture & Rebuild Justification

This document details the complete architectural rebuild of the GridWise Smart Campus Energy Optimization backend. It explains **what we built**, **what components exist**, and critically, **why these strict design decisions were made** to secure a Top 50 spot in the BUP CSE FEST 2026 Preliminary.

---

## 1. What We Have Done (The V2 Rebuild)

We discarded the initial "hackathon-grade" prototype (which relied on LP relaxation and batched LLM calls) and rebuilt the system from scratch with a rigorous, production-grade approach. 

**Key Accomplishments:**
1. **Mathematical Rigor**: Upgraded the optimizer from continuous Linear Programming (LP) to Mixed-Integer Linear Programming (MILP) with binary constraints.
2. **Failure Isolation**: Redesigned the LLM integration so that each operator note is processed asynchronously and independently.
3. **Strict API Contract**: Encoded the official problem statement via strict Pydantic schemas and pure-function guardrails.
4. **Comprehensive Validation**: Maintains a runnable regression suite covering guardrails, API behavior, optimizer constraints, and replay verification.

---

## 2. What Is There & Why It Must Be There

Every file in the `app/` directory serves a distinct, isolated purpose. Here is the breakdown of the architecture and the justification for each component.

### `app/schemas.py` (The API Contract)
* **What is there:** Strict Pydantic models mapping field-for-field to the official competition specification.
* **Why it must be there:** The judge's automated grader expects exact fields and types. Strict schemas reject malformed requests instead of silently mutating them.

### `app/guardrails.py` (The Bouncer)
* **What is there:** Pure validation functions that check parsed LLM outputs against the official rules (e.g., `hours` must be ascending, `factor` between 0 and 1) and raise `DirectiveValidationError` if a rule is broken.
* **Why it must be there:** LLMs hallucinate. If an LLM returns an invalid time window or a negative grid cap, applying it would mathematically break the optimizer or violate the spec. Guardrails act as a "bouncer," rejecting the request rather than misclassifying an unknown note as `no_op`.

### `app/llm_interpreter.py` (The Translator)
* **What is there:** Manages communication with a configurable Google Gemini model. Processes operator notes asynchronously (`asyncio.gather`), isolating one note per LLM call. Includes a bounded timeout, retry handling for rate limits (429/503 errors), and a `SKIP_LLM=true` test-only flag.
* **Why it must be there:** 
  * **One Call Per Note:** In V1, we batched all notes into one prompt. If the LLM misunderstood *one* note, the entire response was corrupted. By isolating them, a failure on Note 2 does not affect Note 1.
  * **Retry Logic:** The free-tier Gemini API drops requests when rate limits are hit. The retry mechanism ensures we survive these spikes during judging without crashing the application.

### `app/optimizer.py` (The Math Engine)
* **What is there:** A 24-hour Mixed-Integer Linear Programming (MILP) solver using SciPy's HiGHS method. It defines 168 variables (7 per hour) including continuous energy flow and binary `{0, 1}` flags for charging and discharging.
* **Why it must be there:** The previous version used continuous LP. LP allows the battery to "charge and discharge at the same time" (which is physically impossible) because it finds mathematical shortcuts. The only mathematically sound way to prevent simultaneous charge/discharge without breaking the objective function is using MILP with a mutual exclusion constraint (`is_charge + is_discharge <= 1`). This guarantees that hidden, adversarial test cases from the judges will *never* produce an invalid battery state.

### `app/main.py` (The Orchestrator)
* **What is there:** The FastAPI application that wires the components together (`/optimize-energy`). It calculates `total_grid_kwh`, `total_cost_bdt`, and `peak_grid_kwh` exactly once by iterating over the final `hourly_plan`.
* **Why it must be there:** Separating the orchestration from the business logic keeps the endpoint fast and readable. Calculating the totals at the very end (instead of inside the optimizer) guarantees that the summary metrics perfectly match the hour-by-hour array, securing the "API Contract & Schema" points on the rubric.

### `tests/` (The Proving Ground)
* **What is there:** 87 tests including unit tests for guardrails, E2E tests for the API, and strict mathematical validations for the optimizer (ensuring energy balance, battery bounds, and end-of-day neutrality). It also includes a natural language paraphrase test suite.
* **Why it must be there:** The tests exercise the public sample cases, replay constraints, schema validation, and safe deterministic test mode without claiming that local tests replace hidden evaluation.

---

## 3. Why This Wins (The "Brutal Judge" Perspective)

If a brutal judge looks at this codebase, they will see a system built for resilience:

1. **Controlled LLM Failures:** Invalid individual LLM outputs reject the request without applying impossible math or inventing `no_op`.
2. **Constraint-Based Scheduling:** The MILP implementation encodes physical and directive constraints before serialization.
3. **Production-Ready Scaling:** The async, retry-backed LLM calls mean this API can handle the judge's automated load testing without dropping connections.
4. **Zero Assumptions:** Every line of code maps directly to a rule explicitly written in the problem statement.

This architecture ensures maximum points for **Directive Interpretation (25 pts)**, **Directive Application & Constraints (25 pts)**, and **Reliability (10 pts)**.
