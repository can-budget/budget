"""Cross-language examples for full same-day topups and the historical cutoff."""

import json
from pathlib import Path

import pytest

from can_api.budget import project

CASES = json.loads(
    (Path(__file__).resolve().parents[3] / "apps/ios/CanTests/today_topup_cases.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_today_topup_projection(case):
    assert (
        project({**case["input"], "topups_today_from": case["topups_today_from"]})
        == case["expected"]
    )


def test_cutoff_retains_all_legacy_examples():
    fixtures = json.loads(
        (Path(__file__).resolve().parents[3] / "apps/ios/CanTests/golden_cases.json").read_text()
    )
    for case in fixtures:
        assert project({**case["input"], "topups_today_from": "9999-12-31"}) == case["expected"], (
            case["name"]
        )


def test_invalid_cutoff_cannot_silently_select_another_policy():
    for cutoff in ("invalid", "2026-01-01", "2026-10-2"):
        with pytest.raises(ValueError):
            project({**CASES[0]["input"], "topups_today_from": cutoff})
