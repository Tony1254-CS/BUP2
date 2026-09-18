"""
LLM-based operator-note interpreter.

Architecture:
  - One async Gemini call per scenario (all notes in a single request).
  - Structured JSON output with response_schema for type safety.
  - Retry with exponential backoff for transient errors.
  - Fallback to a lighter model if primary fails.
  - If interpretation ultimately fails, the request fails (never invents no_op).
  - SKIP_LLM=true is available only with GRIDWISE_TEST_MODE=true.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from dotenv import load_dotenv

from app.guardrails import DirectiveValidationError, validate_directive
from app.schemas import BatteryInput, DirectiveInterpretation

load_dotenv()
logger = logging.getLogger(__name__)

PER_CALL_TIMEOUT = 25.0  # seconds total per scenario (including retries)
MAX_RETRIES = 2
BASE_DELAY = 1.0  # seconds between retries

PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite")

SYSTEM_PROMPT = """\
You are a campus energy scheduling assistant.  You will receive one or more
operator notes and battery parameters.  Your task: decide whether each note
contains a scheduling directive and, if so, classify it.

RULES
  1  Six directive_type values are valid:
     solar_reduction, minimum_battery_reserve, no_charge_window,
     no_discharge_window, max_grid_window, no_op
  2  Time windows are START-INCLUSIVE, END-EXCLUSIVE.
     "from 1 PM to 3 PM" -> hours [13, 14].
     "overnight from 10 PM to 6 AM" -> hours [0, 1, 2, 3, 4, 5, 22, 23].
  3  "Reduce solar by 80%" -> factor = 0.20 (remaining fraction, not the
     percentage removed).
     "Reduce solar by X%" -> factor = (100-X)/100.
     "Solar output at 30%" -> factor = 0.30.
  4  For minimum_battery_reserve, give the absolute energy floor in kWh.
     "Keep battery above 40%" with capacity C -> minimum_energy_kwh = 0.4 * C.
  5  Use no_op only when the note clearly has no effect on the 24-hour
     energy schedule.  Never use no_op merely because wording is unusual
     or because interpretation is uncertain.
  6  Do NOT invent constraints not present in the note.
  7  hours must be a sorted list of integers in ascending order, each 0..23.
     For overnight windows that wrap around midnight, return the hours in
     ascending numeric order.
  8  Interpret each note independently.  Do not transfer constraints from
     one note to another unless the wording explicitly requires it.
  9  structured_adjustment must contain EXACTLY the required keys for the
     directive type and nothing else:
       solar_reduction:         {"hours": [...], "factor": ...}
       minimum_battery_reserve: {"hours": [...], "minimum_energy_kwh": ...}
       no_charge_window:        {"hours": [...]}
       no_discharge_window:     {"hours": [...]}
       max_grid_window:         {"hours": [...], "max_grid_kwh": ...}
       no_op:                   null

OUTPUT — return ONLY a JSON array with one object per note, in order:
[
  {
    "applies": true/false,
    "directive_type": "...",
    "structured_adjustment": { ... } or null,
    "explanation": "one-sentence reason"
  },
  ...
]
"""

# Response schema for Gemini structured output
RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "applies": {"type": "boolean"},
            "directive_type": {
                "type": "string",
                "enum": [
                    "solar_reduction",
                    "minimum_battery_reserve",
                    "no_charge_window",
                    "no_discharge_window",
                    "max_grid_window",
                    "no_op",
                ],
            },
            "structured_adjustment": {},
            "explanation": {"type": "string"},
        },
        "required": ["applies", "directive_type", "structured_adjustment", "explanation"],
    },
}


# ── Module-level Gemini client (lazy singleton) ──────────────────────────
_client = None


def _get_client():
    """Return a reusable Gemini client."""
    global _client
    if _client is None:
        from google import genai
        api_key = os.getenv("GEMINI_API_KEY", "")
        _client = genai.Client(api_key=api_key)
    return _client


def _make_no_op(note_index: int, reason: str = "Defaulted to no_op.") -> dict[str, Any]:
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": reason,
    }


def _is_retryable(exc: Exception) -> bool:
    """Check if the exception is a transient/retryable API error."""
    msg = str(exc).lower()
    retryable_signals = [
        "429", "503", "500", "502", "504", "408",
        "resource_exhausted", "unavailable",
        "deadline", "timeout", "timed out",
        "connection", "temporarily",
    ]
    return any(signal in msg for signal in retryable_signals)


async def _call_gemini(
    notes: list[str], battery: BatteryInput, model: str,
) -> list[dict[str, Any]]:
    """
    Call Gemini with all notes in a single request.
    Returns a list of raw directive dicts (one per note).
    """
    from google.genai import types

    client = _get_client()

    notes_block = "\n".join(
        f'Note {i}: "{note}"' for i, note in enumerate(notes)
    )
    user_prompt = (
        f"Battery parameters:\n"
        f"  capacity_kwh = {battery.capacity_kwh}\n"
        f"  initial_energy_kwh = {battery.initial_energy_kwh}\n"
        f"  minimum_energy_kwh = {battery.minimum_energy_kwh}\n"
        f"  max_charge_kwh_per_hour = {battery.max_charge_kwh_per_hour}\n"
        f"  max_discharge_kwh_per_hour = {battery.max_discharge_kwh_per_hour}\n\n"
        f"Operator notes ({len(notes)} total):\n{notes_block}"
    )

    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    temperature=0.0,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )
            text = response.text
            if text is None:
                raise ValueError("Gemini returned empty text")
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
            return parsed

        except Exception as exc:
            last_exc = exc
            if attempt < MAX_RETRIES and _is_retryable(exc):
                delay = BASE_DELAY * (2 ** attempt)
                retry_match = re.search(r"retryDelay.*?(\d+)", str(exc))
                if retry_match:
                    delay = max(delay, int(retry_match.group(1)) + 1)
                logger.info(
                    "Retryable error (attempt %d/%d, model=%s), waiting %.0fs: %s",
                    attempt + 1, MAX_RETRIES + 1, model, delay, type(exc).__name__
                )
                await asyncio.sleep(delay)
            else:
                raise

    raise last_exc  # type: ignore


async def _interpret_all(
    notes: list[str], battery: BatteryInput,
) -> list[dict[str, Any]]:
    """
    Interpret all notes with timeout + retry + guardrail validation.
    Tries primary model first, then fallback model on failure.
    On guardrail failure of the primary model, retries once with a repair hint.
    """
    models_to_try = [PRIMARY_MODEL]
    if FALLBACK_MODEL and FALLBACK_MODEL != PRIMARY_MODEL:
        models_to_try.append(FALLBACK_MODEL)

    last_exc: Exception | None = None

    for model in models_to_try:
        try:
            raw_list = await asyncio.wait_for(
                _call_gemini(notes, battery, model),
                timeout=PER_CALL_TIMEOUT,
            )

            if len(raw_list) != len(notes):
                raise ValueError(
                    f"Expected {len(notes)} interpretations, got {len(raw_list)}"
                )

            # Validate each interpretation through guardrails
            validated: list[dict[str, Any]] = []
            for i, raw in enumerate(raw_list):
                raw["note_index"] = i
                try:
                    validate_directive(raw, battery)
                    validated.append(raw)
                except DirectiveValidationError as gex:
                    logger.warning(
                        "Note %d: Guardrail rejection (model=%s): %s",
                        i, model, gex,
                    )
                    raise RuntimeError(
                        f"Note {i} failed guardrails: {gex}"
                    ) from gex

            return validated

        except asyncio.TimeoutError:
            logger.warning("Timed out after %.0fs (model=%s)", PER_CALL_TIMEOUT, model)
            last_exc = RuntimeError(f"LLM interpretation timed out (model={model})")

        except Exception as exc:
            logger.warning(
                "Failed (model=%s): %s: %s", model, type(exc).__name__, exc
            )
            last_exc = exc

    raise RuntimeError("LLM interpretation failed after all attempts") from last_exc


async def interpret_notes(
    notes: list[str], battery: BatteryInput
) -> list[DirectiveInterpretation]:
    """
    Interpret all operator notes and return validated directives.

    - SKIP_LLM=true with GRIDWISE_TEST_MODE=true -> all notes become no_op.
    - Otherwise: one async Gemini call for the full scenario.
    """
    skip = os.getenv("SKIP_LLM", "").strip().lower() in ("true", "1", "yes")

    if skip and os.getenv("GRIDWISE_TEST_MODE", "").strip().lower() not in ("true", "1", "yes"):
        raise RuntimeError("SKIP_LLM requires GRIDWISE_TEST_MODE=true")

    if skip:
        logger.info("LLM skipped (SKIP_LLM=%s)", os.getenv("SKIP_LLM", ""))
        raw_list = [_make_no_op(i, "LLM skipped.") for i in range(len(notes))]
    elif not os.getenv("GEMINI_API_KEY", "").strip():
        raise RuntimeError("GEMINI_API_KEY is required when SKIP_LLM is not enabled")
    else:
        raw_list = await _interpret_all(notes, battery)

    return [
        DirectiveInterpretation(**d)
        for d in sorted(raw_list, key=lambda item: item["note_index"])
    ]
