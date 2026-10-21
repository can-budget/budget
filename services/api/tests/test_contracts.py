from uuid import uuid4

import pytest
from pydantic import ValidationError

from can_api.app import app
from can_api.errors import Problem
from can_api.ledger import cursor_decode, cursor_encode
from can_api.schemas import AddEntry, Command, CreateCabinet, SyncRequest


def test_runtime_does_not_serve_interactive_api_documentation():
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
    assert "/v1/auth/password" in app.openapi()["paths"]


def valid_entry():
    return {
        "entry_id": str(uuid4()),
        "sprint_id": str(uuid4()),
        "membership_id": str(uuid4()),
        "kind": "expense",
        "amount_minor": 100,
        "occurred_at": "2026-10-12T08:00:00Z",
        "allow_completed": False,
    }


@pytest.mark.parametrize("amount", [0, -1, 1.5, True, "100", 1_000_000_000])
def test_money_never_coerced(amount):
    with pytest.raises(ValidationError):
        AddEntry.model_validate(valid_entry() | {"amount_minor": amount})


@pytest.mark.parametrize("timestamp", ["2026-10-12T08:00:00", "2026-10-12", 12345678, True])
def test_occurrence_requires_explicit_timezone(timestamp):
    with pytest.raises(ValidationError):
        AddEntry.model_validate(valid_entry() | {"occurred_at": timestamp})


def test_membership_identity_is_required():
    data = valid_entry()
    data.pop("membership_id")
    with pytest.raises(ValidationError):
        AddEntry.model_validate(data)


def test_replacing_budget_requires_explicit_previous_id_and_preview_date():
    payload = {
        "sprint_id": str(uuid4()),
        "initial_minor": 1_100_000,
        "end_date": "2026-11-18",
        "expected_cabinet_revision": 10,
        "replaces_sprint_id": str(uuid4()),
    }
    with pytest.raises(ValidationError):
        Command(command_id=uuid4(), type="create_sprint", payload=payload)
    command = Command(
        command_id=uuid4(),
        type="create_sprint",
        payload={
            **payload,
            "expected_business_date": "2026-10-20",
        },
    )
    assert command.payload["replaces_sprint_id"] == payload["replaces_sprint_id"]


def test_recovery_requires_command_link():
    with pytest.raises(ValidationError):
        AddEntry.model_validate(valid_entry() | {"original_occurred_at": "2026-10-11T08:00:00Z"})


def test_cabinet_timezone_cannot_change_math():
    with pytest.raises(ValidationError):
        CreateCabinet(command_id=uuid4(), name="Budget", timezone="UTC")


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"initial_minor": 100},
        {"bank_minor": None, "end_date": "2026-10-13"},
        {"bank_minor": True},
        {"end_date": "20261013"},
    ],
)
def test_update_requires_actual_dirty_fields(extra):
    with pytest.raises(ValidationError):
        Command.model_validate(
            {
                "command_id": str(uuid4()),
                "type": "update_sprint",
                "payload": {
                    "sprint_id": str(uuid4()),
                    "expected_cabinet_revision": 1,
                    "expected_business_date": "2026-10-12",
                    **extra,
                },
            }
        )


def test_explicit_false_is_required_for_boolean_value():
    with pytest.raises(ValidationError):
        AddEntry.model_validate(valid_entry() | {"allow_completed": "false"})


def test_sync_ids_are_bounded_and_unique():
    with pytest.raises(ValidationError):
        SyncRequest(unresolved_command_ids=[uuid4() for _ in range(1001)])
    value = uuid4()
    with pytest.raises(ValidationError):
        SyncRequest(unresolved_command_ids=[value, value])


def test_cursor_rejects_arbitrary_structure():
    value = ["2026-10-12T08:00:00+00:00", str(uuid4())]
    assert cursor_decode(cursor_encode(value)) == value
    with pytest.raises(Problem):
        cursor_decode("not-valid-json")


def test_routes_preserve_handoff_methods():
    paths = app.openapi()["paths"]
    assert "post" in paths["/v1/devices"]
    assert "post" in paths["/v1/cabinets/{cabinet_id}/commands"]
    assert "post" in paths["/v1/cabinets/{cabinet_id}/sync"]
    assert "delete" in paths["/v1/me"]
    assert "requestBody" not in paths["/v1/devices/{device_id}/widget-token"]["post"]
