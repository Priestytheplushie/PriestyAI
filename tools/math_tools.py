import math
import cmath
import logging
from typing import Any
from tools.registry import tool_registry, ToolExecutionContext

logger = logging.getLogger("PriestyAI.MathTools")

SAFE_MATH_NAMESPACE = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "sqrt": math.sqrt,
    "cbrt": lambda x: x ** (1 / 3),
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "exp": math.exp,
    "pow": math.pow,
    "abs": abs,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "factorial": math.factorial,
    "comb": math.comb,
    "perm": math.perm,
    "gcd": math.gcd,
    "lcm": math.lcm,
    "degrees": math.degrees,
    "radians": math.radians,
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "nan": math.nan,
}

@tool_registry.register(
    name="calc",
    description=(
        "Instant in-memory high-precision mathematical and scientific calculation tool (<1ms latency).\n"
        "Use this for arithmetic, trigonometry, powers, roots, logarithms, combinations, probability, or percentages.\n"
        "Example expressions:\n"
        "- '1.07**15 * 5000'\n"
        "- 'factorial(20) / (factorial(5) * factorial(15))'\n"
        "- 'sqrt(144) + log2(1024)'\n"
        "- 'sin(radians(45)) * cos(radians(45))'"
    )
)
def calc(expression: str, context: ToolExecutionContext = None) -> dict[str, Any]:
    cleaned_expr = expression.strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    logger.info(f"[calc] Evaluating math expression: '{cleaned_expr}'")

    try:
        result = eval(cleaned_expr, {"__builtins__": None}, SAFE_MATH_NAMESPACE)

        if isinstance(result, float):
            if result.is_integer():
                formatted_res = str(int(result))
            else:
                formatted_res = f"{result:.10g}"
        else:
            formatted_res = str(result)

        return {
            "status": "success",
            "expression": expression,
            "result": formatted_res
        }
    except Exception as e:
        logger.warning(f"[calc] Math evaluation failed for '{expression}': {e}")
        return {
            "status": "error",
            "expression": expression,
            "error": f"Evaluation error: {str(e)}"
        }