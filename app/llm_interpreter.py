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

GLOBAL_TIMEOUT = 4.5  # Strict budget to ensure p95 < 5s

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
            "structured_adjustment": {
                "type": "object",
                "properties": {
                    "hours": {"type": "array", "items": {"type": "integer"}},
                    "factor": {"type": "number"},
                    "minimum_energy_kwh": {"type": "number"},
                    "max_grid_kwh": {"type": "number"}
                }
            },
            "explanation": {"type": "string"},
        },
        "required": ["applies", "directive_type", "explanation"],
    },
}

_client = None

def _get_client():
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

async def _call_gemini(
    notes: list[str], battery: BatteryInput, model: str, repair_error: str = None
) -> list[dict[str, Any]]:
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

    if repair_error:
        user_prompt += f"\n\nYOUR PREVIOUS OUTPUT FAILED VALIDATION:\n{repair_error}\nPlease fix the output to comply strictly with the rules."

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

def _validate_all(raw_list: list[dict[str, Any]], notes: list[str], battery: BatteryInput, model: str) -> list[dict[str, Any]]:
    if len(raw_list) != len(notes):
        raise ValueError(f"Expected {len(notes)} interpretations, got {len(raw_list)}")

    validated = []
    for i, raw in enumerate(raw_list):
        raw["note_index"] = i
        try:
            validate_directive(raw, battery)
            validated.append(raw)
        except DirectiveValidationError as gex:
            logger.warning("Note %d: Guardrail rejection (model=%s): %s", i, model, gex)
            raise RuntimeError(f"Note {i} failed guardrails: {gex}") from gex
    return validated

async def _interpret_all(
    notes: list[str], battery: BatteryInput,
) -> list[dict[str, Any]]:
    try:
        async with asyncio.timeout(GLOBAL_TIMEOUT):
            # Attempt 1: Primary
            try:
                raw_list = await _call_gemini(notes, battery, PRIMARY_MODEL)
                return _validate_all(raw_list, notes, battery, PRIMARY_MODEL)
            except Exception as e:
                # Attempt 2: Repair with Primary
                if isinstance(e, RuntimeError) and "failed guardrails" in str(e):
                    try:
                        raw_list = await _call_gemini(notes, battery, PRIMARY_MODEL, repair_error=str(e))
                        return _validate_all(raw_list, notes, battery, PRIMARY_MODEL)
                    except Exception:
                        pass # Fall through to fallback
                else:
                    pass # Fall through to fallback
            
            # Attempt 3: Fallback (Once)
            raw_list = await _call_gemini(notes, battery, FALLBACK_MODEL)
            return _validate_all(raw_list, notes, battery, FALLBACK_MODEL)
            
    except asyncio.TimeoutError:
        logger.warning("Global timeout exceeded after %.1fs", GLOBAL_TIMEOUT)
        raise RuntimeError("LLM interpretation exceeded global timeout budget")

async def interpret_notes(
    notes: list[str], battery: BatteryInput
) -> list[DirectiveInterpretation]:
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
