"""Three fixed scenarios (FR-FIN-4). Endpoint rule per input: config.ENDPOINT_DIRECTION."""

from app.finance import config
from app.finance.engine import FinanceResult, evaluate
from app.finance.models import FinanceInputs, PointInputs


def scenario_point(inputs: FinanceInputs, scenario: str) -> PointInputs:
    if scenario not in config.SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; expected one of {config.SCENARIOS}")
    values: dict[str, float] = {}
    for key, rng in inputs.ranges().items():
        if scenario == "base":
            values[key] = rng.base
        else:
            higher_is_better = config.ENDPOINT_DIRECTION[key]
            favorable = scenario == "optimistic"
            # favorable endpoint of a higher-is-better input is `high`; adverse is `low`; costs invert
            values[key] = rng.high if favorable == higher_is_better else rng.low
    return PointInputs(starting_budget=inputs.starting_budget, **values)


def run_scenarios(inputs: FinanceInputs) -> dict[str, FinanceResult]:
    return {name: evaluate(scenario_point(inputs, name)) for name in config.SCENARIOS}
