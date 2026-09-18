import logging
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.models.request import OptimizeRequest
from app.models.response import OptimizeResponse, HealthResponse
from app.validation.input_validator import validate_input_scenario, InputValidationError
from app.validation.directive_validator import DirectiveValidationError
from app.validation.schedule_validator import ScheduleValidationError
from app.llm.service import parse_operator_notes
from app.optimizer.solver import solve_optimization, OptimizationError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gridwise")

app = FastAPI(
    title="GridWise Energy Optimization API",
    description="AI-assisted energy management & microgrid schedule optimizer.",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check():
    """Health check endpoint returning system status."""
    return HealthResponse(status="ok")

@app.post("/optimize", response_model=OptimizeResponse, tags=["Optimization"])
@app.post("/optimize-energy", response_model=OptimizeResponse, tags=["Optimization"])
def optimize_schedule(request: OptimizeRequest):
    """
    Main optimization endpoint.
    1. Validates input scenario.
    2. Parses operator notes via LLM into structured directives.
    3. Solves 24-hour linear optimization program for grid & battery dispatch.
    4. Replays and validates energy schedule constraints.
    5. Returns optimal hourly plan and summary.
    """
    # 1. Semantic input validation
    try:
        validate_input_scenario(request)
    except InputValidationError as e:
        logger.warning(f"Input validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid input scenario: {str(e)}"
        )

    # 2. LLM Directive Parsing & Validation
    try:
        directives = parse_operator_notes(request.operator_notes, request.battery)
    except DirectiveValidationError as e:
        logger.error(f"Directive validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Directive interpretation error: {str(e)}"
        )
    except Exception as e:
        logger.error(f"LLM processing failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"LLM directive parsing failed: {str(e)}"
        )

    # 3. Mathematical Optimization & Post-Replay Validation
    try:
        hourly_plan, total_grid, total_cost, peak_grid, summary = solve_optimization(
            request, directives
        )
    except OptimizationError as e:
        logger.error(f"Optimization infeasible: {e}")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Infeasible schedule optimization: {str(e)}"
        )
    except ScheduleValidationError as e:
        logger.error(f"Schedule replay validation failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Schedule validation failed after solve: {str(e)}"
        )

    # 4. Construct response
    return OptimizeResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=directives,
        hourly_plan=hourly_plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary=summary
    )
