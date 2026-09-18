import json
import re
import logging
from typing import List, Dict, Any, Optional
from app.config import settings
from app.models.directives import DirectiveInterpretation, DirectiveType
from app.models.request import BatteryConfig
from app.validation.directive_validator import validate_directive_interpretations

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an expert energy grid directive parser.
Given a list of operator notes, interpret each note into a structured JSON directive object.
Output MUST be a valid JSON array containing exactly one JSON object per operator note in the input list.

Allowed directive_type values:
1. "solar_reduction": Solar generation drops by a factor. Required structured_adjustment keys: {"hours": [int, ...], "factor": float (0.0 to 1.0)}.
2. "minimum_battery_reserve": High battery reserve requirement. Required structured_adjustment keys: {"hours": [int, ...], "minimum_energy_kwh": float}.
3. "no_charge_window": Battery charging is forbidden during these hours. Required structured_adjustment keys: {"hours": [int, ...]}.
4. "no_discharge_window": Battery discharging is forbidden during these hours. Required structured_adjustment keys: {"hours": [int, ...]}.
5. "max_grid_window": Grid power import is capped. Required structured_adjustment keys: {"hours": [int, ...], "max_grid_kwh": float}.
6. "no_op": Irrelevant or non-operational note (e.g., cafeteria, general news). MUST set applies: false, directive_type: "no_op", structured_adjustment: null.

Rules:
- note_index must equal the 0-based position of the note in the input array.
- hours must be integers between 0 and 23 inclusive, sorted in ascending order with no duplicates.
- Time conversions: 12 AM = 0, 1 AM = 1, ..., 12 PM = 12, 1 PM = 13, 2 PM = 14, 3 PM = 15, 4 PM = 16, etc.
- Example: "1 PM to 3 PM" covers hours [13, 14, 15]. "2 PM to 4 PM" covers hours [14, 15, 16].
- Output format: JSON array only. No markdown formatting or extra text.
"""

def parse_time_range(text: str) -> List[int]:
    """Helper to convert time ranges like '1 PM to 3 PM' or '13 to 15' or '13:00-15:00' into hour lists."""
    pattern = r'(\d{1,2})\s*(AM|PM|am|pm)?\s*(?:to|-|until)\s*(\d{1,2})\s*(AM|PM|am|pm)?'
    match = re.search(pattern, text)
    if not match:
        return []
    
    start_num = int(match.group(1))
    start_ampm = match.group(2)
    end_num = int(match.group(3))
    end_ampm = match.group(4)
    
    # Infer AM/PM if missing
    if start_ampm is None and end_ampm is not None:
        start_ampm = end_ampm
    if end_ampm is None and start_ampm is not None:
        end_ampm = start_ampm

    def to_24(num: int, ampm: Optional[str]) -> int:
        if ampm:
            ampm_upper = ampm.upper()
            if ampm_upper == "PM" and num < 12:
                return num + 12
            if ampm_upper == "AM" and num == 12:
                return 0
        return num

    start_h = to_24(start_num, start_ampm)
    end_h = to_24(end_num, end_ampm)
    
    if start_h <= end_h:
        return list(range(start_h, end_h + 1))
    else:
        # Crosses midnight
        return list(range(start_h, 24)) + list(range(0, end_h + 1))

def fallback_rule_parser(operator_notes: List[str], battery: BatteryConfig) -> List[DirectiveInterpretation]:
    """Deterministic rule-based fallback parser when LLM is unavailable or fails."""
    results: List[DirectiveInterpretation] = []

    for idx, note in enumerate(operator_notes):
        note_lower = note.lower()

        # Check non-operational / cafeteria / menu notes
        if any(w in note_lower for w in ["cafeteria", "menu", "lunch", "dinner", "weather forecast tomorrow"]):
            results.append(DirectiveInterpretation(
                note_index=idx,
                applies=False,
                directive_type=DirectiveType.NO_OP,
                structured_adjustment=None,
                explanation="Non-operational note ignored."
            ))
            continue

        # Check Solar reduction
        if "solar" in note_lower or "pv" in note_lower or "sun" in note_lower:
            hours = parse_time_range(note)
            if not hours:
                hours = [13, 14, 15]  # Default fallback if unparseable
            
            # Extract percentage/factor
            percent_match = re.search(r'(\d+)\s*%', note)
            if percent_match:
                factor = float(percent_match.group(1)) / 100.0
            else:
                factor = 0.2
            
            results.append(DirectiveInterpretation(
                note_index=idx,
                applies=True,
                directive_type=DirectiveType.SOLAR_REDUCTION,
                structured_adjustment={"hours": sorted(list(set(hours))), "factor": round(factor, 4)},
                explanation=f"Solar output reduced to {factor*100}% during specified hours."
            ))
            continue

        # Check No charge window
        if "do not charge" in note_lower or "no charge" in note_lower or "stop charging" in note_lower:
            hours = parse_time_range(note)
            if not hours:
                hours = [14, 15, 16]
            results.append(DirectiveInterpretation(
                note_index=idx,
                applies=True,
                directive_type=DirectiveType.NO_CHARGE_WINDOW,
                structured_adjustment={"hours": sorted(list(set(hours)))},
                explanation="Battery charging prohibited during specified window."
            ))
            continue

        # Check No discharge window
        if "do not discharge" in note_lower or "no discharge" in note_lower or "stop discharging" in note_lower:
            hours = parse_time_range(note)
            if not hours:
                hours = [18, 19, 20]
            results.append(DirectiveInterpretation(
                note_index=idx,
                applies=True,
                directive_type=DirectiveType.NO_DISCHARGE_WINDOW,
                structured_adjustment={"hours": sorted(list(set(hours)))},
                explanation="Battery discharging prohibited during specified window."
            ))
            continue

        # Check Min battery reserve
        if "reserve" in note_lower or "minimum energy" in note_lower or "keep battery above" in note_lower:
            hours = parse_time_range(note)
            if not hours:
                hours = list(range(0, 24))
            val_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:kwh|kwh)', note_lower)
            min_e = float(val_match.group(1)) if val_match else battery.capacity_kwh * 0.3
            min_e = min(min_e, battery.capacity_kwh)

            results.append(DirectiveInterpretation(
                note_index=idx,
                applies=True,
                directive_type=DirectiveType.MINIMUM_BATTERY_RESERVE,
                structured_adjustment={"hours": sorted(list(set(hours))), "minimum_energy_kwh": round(min_e, 2)},
                explanation="Minimum battery reserve requirement adjusted."
            ))
            continue

        # Check Max grid window
        if "limit grid" in note_lower or "max grid" in note_lower or "cap grid" in note_lower:
            hours = parse_time_range(note)
            if not hours:
                hours = list(range(0, 24))
            val_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:kwh|kwh)', note_lower)
            max_g = float(val_match.group(1)) if val_match else 50.0

            results.append(DirectiveInterpretation(
                note_index=idx,
                applies=True,
                directive_type=DirectiveType.MAX_GRID_WINDOW,
                structured_adjustment={"hours": sorted(list(set(hours))), "max_grid_kwh": round(max_g, 2)},
                explanation="Maximum grid power capped during window."
            ))
            continue

        # Fallback for unrecognized note
        results.append(DirectiveInterpretation(
            note_index=idx,
            applies=False,
            directive_type=DirectiveType.NO_OP,
            structured_adjustment=None,
            explanation="Unrecognized operator note treated as no_op."
        ))

    return results

def parse_operator_notes(operator_notes: List[str], battery: BatteryConfig) -> List[DirectiveInterpretation]:
    """
    Parses operator notes into structured directive interpretations.
    First attempts LLM parsing (via google-genai / openai), fallback to rule-based parser.
    Ensures output is validated against directive_validator rules.
    """
    if not settings.LLM_API_KEY or settings.LLM_API_KEY == "your_llm_api_key_here":
        logger.info("No valid LLM API key configured. Using deterministic rule fallback parser.")
        results = fallback_rule_parser(operator_notes, battery)
        validate_directive_interpretations(results, len(operator_notes), battery)
        return results

    try:
        if settings.LLM_PROVIDER.lower() == "google":
            from google import genai
            client = genai.Client(api_key=settings.LLM_API_KEY)
            prompt = f"{SYSTEM_PROMPT}\n\nOperator Notes:\n" + json.dumps(operator_notes, indent=2)
            response = client.models.generate_content(
                model=settings.LLM_MODEL,
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            raw_text = response.text
        else:
            from openai import OpenAI
            client = OpenAI(api_key=settings.LLM_API_KEY)
            prompt = f"{SYSTEM_PROMPT}\n\nOperator Notes:\n" + json.dumps(operator_notes, indent=2)
            response = client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                response_format={"type": "json_object"}
            )
            raw_text = response.choices[0].message.content

        # Clean JSON markdown fences if present
        cleaned_text = raw_text.strip()
        if cleaned_text.startswith("```json"):
            cleaned_text = cleaned_text[7:]
        if cleaned_text.endswith("```"):
            cleaned_text = cleaned_text[:-3]
        cleaned_text = cleaned_text.strip()

        data = json.loads(cleaned_text)
        if isinstance(data, dict) and "directives" in data:
            data = data["directives"]

        interpretations = [DirectiveInterpretation(**item) for item in data]
        validate_directive_interpretations(interpretations, len(operator_notes), battery)
        return interpretations

    except Exception as e:
        logger.warning(f"LLM parsing failed or generated invalid schema: {e}. Falling back to rule parser.")
        results = fallback_rule_parser(operator_notes, battery)
        validate_directive_interpretations(results, len(operator_notes), battery)
        return results
