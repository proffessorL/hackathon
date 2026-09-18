import math
from typing import List, Dict, Any
from app.models.directives import DirectiveInterpretation, DirectiveType
from app.models.request import BatteryConfig

class DirectiveValidationError(Exception):
    """Raised when LLM output fails deterministic validation."""
    pass

def validate_directive_interpretations(
    interpretations: List[DirectiveInterpretation],
    num_notes: int,
    battery: BatteryConfig
) -> None:
    """
    Deterministically validates LLM interpretation output.
    Raises DirectiveValidationError if output is malformed or invalid.
    """
    if len(interpretations) != num_notes:
        raise DirectiveValidationError(
            f"Expected {num_notes} directive interpretations, got {len(interpretations)}"
        )

    for idx, inter in enumerate(interpretations):
        # 1 & 2. Check note index alignment
        if inter.note_index != idx:
            raise DirectiveValidationError(
                f"Interpretation at position {idx} has note_index {inter.note_index}, expected {idx}"
            )

        # 1. Directive type check
        if inter.directive_type not in DirectiveType.__members__.values() and inter.directive_type not in list(DirectiveType):
            raise DirectiveValidationError(f"Unsupported directive_type: {inter.directive_type}")

        # 7 & 8. applies semantics and no_op check
        if inter.directive_type == DirectiveType.NO_OP:
            if inter.applies is not False:
                raise DirectiveValidationError("For no_op directive, applies MUST be false.")
            if inter.structured_adjustment is not None:
                raise DirectiveValidationError("For no_op directive, structured_adjustment MUST be null.")
        else:
            if inter.applies is not True:
                raise DirectiveValidationError(f"For directive {inter.directive_type}, applies MUST be true.")
            if inter.structured_adjustment is None:
                raise DirectiveValidationError(f"For directive {inter.directive_type}, structured_adjustment MUST NOT be null.")

        if inter.structured_adjustment is not None:
            adj = inter.structured_adjustment

            # 3. hours validation for applicable directives
            if "hours" not in adj or not isinstance(adj["hours"], list):
                raise DirectiveValidationError(f"Directive {inter.directive_type} missing 'hours' list.")
            
            hours = adj["hours"]
            if not hours:
                raise DirectiveValidationError(f"Directive {inter.directive_type} hours list cannot be empty.")
            
            for h in hours:
                if not isinstance(h, int) or isinstance(h, bool):
                    raise DirectiveValidationError(f"Hour {h} in directive must be an integer.")
                if h < 0 or h > 23:
                    raise DirectiveValidationError(f"Hour {h} in directive must be between 0 and 23.")
            
            if hours != sorted(hours):
                raise DirectiveValidationError(f"Hours {hours} must be sorted in strictly ascending order.")
            
            if len(hours) != len(set(hours)):
                raise DirectiveValidationError(f"Hours {hours} contains duplicate values.")

            # Type specific validation
            if inter.directive_type == DirectiveType.SOLAR_REDUCTION:
                if "factor" not in adj:
                    raise DirectiveValidationError("solar_reduction requires 'factor'.")
                factor = adj["factor"]
                if not isinstance(factor, (int, float)) or not math.isfinite(factor):
                    raise DirectiveValidationError("solar_reduction factor must be a finite number.")
                if factor < 0.0 or factor > 1.0:
                    raise DirectiveValidationError(f"solar_reduction factor must be in [0.0, 1.0], got {factor}")

            elif inter.directive_type == DirectiveType.MINIMUM_BATTERY_RESERVE:
                if "minimum_energy_kwh" not in adj:
                    raise DirectiveValidationError("minimum_battery_reserve requires 'minimum_energy_kwh'.")
                min_e = adj["minimum_energy_kwh"]
                if not isinstance(min_e, (int, float)) or not math.isfinite(min_e):
                    raise DirectiveValidationError("minimum_energy_kwh must be a finite number.")
                if min_e < 0:
                    raise DirectiveValidationError(f"minimum_energy_kwh must be non-negative, got {min_e}")
                if min_e > battery.capacity_kwh:
                    raise DirectiveValidationError(
                        f"minimum_energy_kwh ({min_e}) cannot exceed battery capacity ({battery.capacity_kwh})"
                    )

            elif inter.directive_type == DirectiveType.MAX_GRID_WINDOW:
                if "max_grid_kwh" not in adj:
                    raise DirectiveValidationError("max_grid_window requires 'max_grid_kwh'.")
                max_g = adj["max_grid_kwh"]
                if not isinstance(max_g, (int, float)) or not math.isfinite(max_g):
                    raise DirectiveValidationError("max_grid_kwh must be a finite number.")
                if max_g < 0:
                    raise DirectiveValidationError(f"max_grid_kwh must be non-negative, got {max_g}")

            elif inter.directive_type in (DirectiveType.NO_CHARGE_WINDOW, DirectiveType.NO_DISCHARGE_WINDOW):
                # No extra fields required beyond hours
                pass
