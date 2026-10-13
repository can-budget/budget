"""Can calculation_version=2: deterministic integer arithmetic, NOT a backend.

Dates are cabinet-local canonical dates. `end_date` is the ORIGINAL end date.
Revisions have an effective day, increasing sequence, fixed bank adjustment and
optional new end date. Bank-adjustment deltas have already been accepted by the
server; NEVER derive them again from a target balance during replay.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any

MAX_OPERATION = 999_999_999
MAX_EXPOSURE = 9_000_000_000_000


def _integer(value: Any, name: str, maximum: int = MAX_EXPOSURE) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{name} must be integer 0..{maximum}")
    return value


def _signed(value: Any, name: str) -> int:
    if type(value) is not int or abs(value) > MAX_EXPOSURE:
        raise ValueError(f"{name} must be a signed integer within exposure limit")
    return value


def _date(value: str) -> date:
    if not isinstance(value, str):
        raise ValueError("date must be YYYY-MM-DD")
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("date must be canonical YYYY-MM-DD")
    return parsed


def project(data: dict[str, Any]) -> dict[str, Any]:
    """Replay totals, accepted revisions and carry modes to one business date.

    Aggregate rows may repeat a day. Topups plus signed adjustments are summed
    BEFORE division. End-date changes affect their own day and future days only.
    Security, atomic target-to-delta conversion, idempotency and concurrency are
    the calling service's responsibilities; they are not tested by this module.
    """
    initial = _integer(data["initial_minor"], "initial_minor", MAX_OPERATION)
    if initial == 0:
        raise ValueError("initial_minor must be positive")
    start, original_end, today = (_date(data[k]) for k in ("start_date", "end_date", "as_of_date"))
    if not 0 <= (original_end - start).days <= 365 or today < start:
        raise ValueError("invalid sprint interval/as_of_date")
    exposure = initial
    revisions = []
    for item in data.get("revisions", []):
        d = _date(item["effective_day"])
        seq = _integer(item["sequence"], "sequence")
        if seq == 0:
            raise ValueError("sequence must be positive")
        adjustment = _signed(item.get("bank_adjustment_minor", 0), "bank_adjustment_minor")
        end = _date(item["end_date"]) if item.get("end_date") is not None else None
        revisions.append((d, seq, adjustment, end))
        exposure += abs(adjustment)
        if exposure > MAX_EXPOSURE:
            raise ValueError("exposure limit exceeded")
    revisions.sort(key=lambda r: r[1])
    by_day: dict[date, list[tuple]] = {}
    prior_day, prior_seq, cursor_end = start, 0, original_end
    for d, seq, adjustment, end in revisions:
        if seq <= prior_seq or d < prior_day or not start <= d <= cursor_end:
            raise ValueError("revisions must be unique, chronological and within active sprint")
        if end is not None:
            if not d <= end <= start + timedelta(days=365):
                raise ValueError("invalid revised end date")
            cursor_end = end
        by_day.setdefault(d, []).append((d, seq, adjustment, end))
        prior_day, prior_seq = d, seq

    def end_for_day(day: date) -> date:
        end = original_end
        for effective, _, _, new_end in revisions:
            if effective > day:
                break
            if new_end is not None:
                end = new_end
        return end

    totals: dict[date, list[int]] = {}
    for item in data.get("days", []):
        day = _date(item["day"])
        if not start <= day <= end_for_day(day):
            raise ValueError("aggregate outside effective sprint dates")
        p = _integer(item.get("topups_minor", 0), "topups_minor")
        e = _integer(item.get("expenses_minor", 0), "expenses_minor")
        exposure += p + e
        if exposure > MAX_EXPOSURE:
            raise ValueError("exposure limit exceeded")
        row = totals.setdefault(day, [0, 0])
        row[0] += p
        row[1] += e
    choices: dict[date, str] = {}
    for key, mode in data.get("choices", {}).items():
        day = _date(key)
        if not start <= day <= end_for_day(day) or mode not in ("today", "distribute"):
            raise ValueError("invalid carry decision")
        choices[day] = mode
    bank, previous_available, day, end = initial, 0, start, original_end
    last: dict[str, Any] = {}
    while day <= today and day <= end:
        adjustment = 0
        for _, _, delta, revised_end in by_day.get(day, []):
            adjustment += delta
            if revised_end is not None:
                end = revised_end
        n = (end - day).days + 1
        carry = max(previous_available, 0) if day > start else 0
        p, e = totals.get(day, [0, 0])
        inflow = p + adjustment
        distributed = bank // n + inflow // n - e
        spend_today = carry + (bank - carry) // n + inflow // n - e
        selectable = day > start and n > 1 and carry > 0
        stored = choices.get(day)
        available = spend_today if stored == "today" else distributed
        bank += inflow - e
        state = (f"selected_{stored}" if stored else
                 ("pending" if day == today else "auto_distribute")) if selectable else "none"
        last = dict(status="active", bank_minor=bank, today_remaining_minor=available,
                    days_remaining=n, carry_minor=carry, carry_status=state,
                    today_mode_remaining_minor=spend_today if selectable else None,
                    distribute_remaining_minor=distributed if selectable else None)
        previous_available = available
        day += timedelta(days=1)
    if today > end:
        return dict(status="completed", bank_minor=bank, today_remaining_minor=None,
                    days_remaining=0, carry_minor=0, carry_status="none",
                    today_mode_remaining_minor=None, distribute_remaining_minor=None)
    return last
