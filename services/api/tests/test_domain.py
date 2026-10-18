import json
from pathlib import Path

import pytest

from can_api.budget import MAX_EXPOSURE, project

FIXTURES = (
    Path(__file__).resolve().parents[3] / "Can_Developer_Handoff_v1.1/reference/golden_cases.json"
)


@pytest.mark.parametrize("case", json.loads(FIXTURES.read_text()), ids=lambda case: case["name"])
def test_reference_parity(case):
    assert project(case["input"]) == case["expected"]


def test_opposing_adjustments_still_consume_gross_exposure():
    data = {
        "initial_minor": 100,
        "start_date": "2026-10-12",
        "end_date": "2026-10-12",
        "as_of_date": "2026-10-12",
        "revisions": [
            {
                "sequence": 1,
                "effective_day": "2026-10-12",
                "bank_adjustment_minor": MAX_EXPOSURE // 2,
            },
            {
                "sequence": 2,
                "effective_day": "2026-10-12",
                "bank_adjustment_minor": -MAX_EXPOSURE // 2,
            },
        ],
    }
    with pytest.raises(ValueError, match="exposure"):
        project(data)


def test_late_expense_does_not_rederive_target_delta():
    base = {
        "initial_minor": 1_000_000,
        "start_date": "2026-10-12",
        "end_date": "2026-10-21",
        "as_of_date": "2026-10-12",
        "days": [{"day": "2026-10-12", "expenses_minor": 120_000}],
        "revisions": [
            {"sequence": 1, "effective_day": "2026-10-12", "bank_adjustment_minor": 20_000}
        ],
    }
    assert project(base)["bank_minor"] == 900_000
    base["days"][0]["expenses_minor"] += 10_000
    assert project(base)["bank_minor"] == 890_000
