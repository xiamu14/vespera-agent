"""Client-independent Ani Bot configuration."""

import math
import os


DEFAULT_DAILY_BUDGET_USD = 1.0


def daily_budget_usd():
    value = os.environ.get("VESPERA_DAILY_BUDGET_USD", str(DEFAULT_DAILY_BUDGET_USD))
    try:
        budget = float(value)
    except ValueError as exc:
        raise ValueError("VESPERA_DAILY_BUDGET_USD must be a positive finite number") from exc
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("VESPERA_DAILY_BUDGET_USD must be a positive finite number")
    return budget
