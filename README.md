# GridWise LLM — BUP CSE FEST 2026

LLM-assisted 24-hour campus energy optimizer for the BUP CSE FEST 2026 Hackathon Online Preliminary.

## Architecture

```
POST /optimize-energy
  │
  ├─ 1. LLM Interpreter (Google Gemini, configurable via GEMINI_MODEL)
  │     • One async call PER operator note (never batched)
  │     • 20s bounded timeout per call, bounded retries, asyncio.gather
  │     • Guardrail validation (invalid interpretation fails the request)
  │
  ├─ 2. MILP Optimizer (SciPy HiGHS)
  │     • Binary is_charge[h] + is_discharge[h] with mutual exclusion
  │     • 168 variables (7 per hour × 24 hours)
  │     • All constraints traceable to official spec
  │
  ├─ 3. Final Schedule Verifier
  │     • Replays energy balance, battery, directive, and neutrality rules
  │
  └─ 4. Totals Computation
        • Computed exactly once after verification from the final hourly_plan
```

## Quick Start

```bash
# 1. Install
pip install -r requirements.txt

# 2. Set API key and optional model
$env:GEMINI_API_KEY="your_key_here"
$env:GEMINI_MODEL="gemini-2.5-flash"

# 3. Run
uvicorn app.main:app --port 8000

# 4. Test
pytest tests/ -v
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes in production | Google Gemini API key |
| `GEMINI_MODEL` | No | Model name; defaults to `gemini-2.5-flash` |
| `SKIP_LLM` | Tests only | Set `true` only together with `GRIDWISE_TEST_MODE=true` |
| `GRIDWISE_TEST_MODE` | Tests only | Explicitly enables deterministic local test mode |

## API Endpoints

### `GET /health`
Returns `{"status": "ok"}`.

### `POST /optimize-energy`
Accepts the full input payload (scenario_id, operator_notes, hours, battery).
Returns the optimized schedule with directive interpretations.

```bash
curl http://localhost:8000/health
$body = (Get-Content sample_cases.json | ConvertFrom-Json).cases[0].input |
  ConvertTo-Json -Depth 10
Invoke-RestMethod http://localhost:8000/optimize-energy -Method Post `
  -ContentType "application/json" -Body $body
```

The request must contain 24 unique hours (`0` through `23`) and 1–3
non-empty operator notes. Invalid JSON or structurally invalid requests return
`400`; controlled runtime failures return `500`.

## Testing

```bash
# All tests (guardrails + optimizer + e2e)
pytest tests/ -v

# Optimizer only (no LLM)
pytest tests/test_optimizer.py -v

# Paraphrase tests (requires GEMINI_API_KEY)
pytest tests/test_paraphrase.py -v -s
```

## Docker

```bash
docker build -t gridwise .
docker run -p 8000:8000 -e GEMINI_API_KEY=your_key gridwise
```

The image binds to `0.0.0.0:8000` and includes a `/health` Docker healthcheck.
Build and test the image locally before publishing it with an immutable tag or
digest for organizer fallback evaluation.

## Known limitations

- The service requires a reachable Gemini API and valid quota for
  LLM-backed interpretation.
- An invalid, unavailable, or timed-out LLM response fails the request with a
  controlled `500`; it is never silently converted to `no_op`.
- `no_op` is reserved for an LLM-established irrelevant note.
- `SKIP_LLM` is intended only for local tests and requires
  `GRIDWISE_TEST_MODE=true`; neither variable should be enabled for judging.
