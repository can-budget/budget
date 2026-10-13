"""Check the implementation contract, including negative receipt/format cases."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import yaml
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    document = yaml.safe_load((ROOT / "contracts/openapi.yaml").read_text())
    schemas = document["components"]["schemas"]
    references = 0

    def visit(value: object) -> None:
        nonlocal references
        if isinstance(value, dict):
            if "$ref" in value:
                reference = value["$ref"]
                assert reference.startswith("#/"), f"External reference: {reference}"
                target = document
                for component in reference[2:].split("/"):
                    target = target[component.replace("~1", "/").replace("~0", "~")]
                references += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)

    def valid(name: str, value: object, expected: bool = True) -> None:
        validator = Draft202012Validator(
            {
                "$ref": f"#/components/schemas/{name}",
                "components": document["components"],
            },
            format_checker=FormatChecker(),
        )
        errors = list(validator.iter_errors(value))
        assert (not errors) == expected, f"{name}: expected valid={expected}; {errors}"

    command_id = str(uuid4())
    result = {
        "command_id": command_id,
        "status": "applied",
        "result_revision": 1,
        "entity_id": None,
        "invitation": None,
    }
    error = {
        "code": "entry_outside_sprint",
        "message": "Date is outside the sprint",
        "request_id": str(uuid4()),
        "retryable": False,
        "details": {},
    }
    receipt = {
        "command_id": command_id,
        "status": "applied",
        "result_revision": 1,
        "result": result,
        "error": None,
    }
    valid("CommandReceipt", receipt)
    valid("CommandReceipt", {**receipt, "error": error}, False)
    valid("CommandReceipt", {**receipt, "result": None}, False)
    valid("CommandReceipt", {**receipt, "status": "unknown"}, False)
    unknown = {**receipt, "status": "unknown", "result_revision": None, "result": None}
    valid("CommandReceipt", unknown)
    valid("CommandReceipt", {**unknown, "error": error}, False)
    rejected = {**unknown, "status": "rejected", "error": error}
    valid("CommandReceipt", rejected)
    valid("CommandReceipt", {**rejected, "error": None}, False)
    valid("CommandReceipt", {**rejected, "result_revision": 5}, False)
    valid("CommandReceipt", {**unknown, "command_id": "not-a-uuid"}, False)

    command = {
        "command_id": command_id,
        "type": "add_entry",
        "payload": {
            "entry_id": str(uuid4()),
            "sprint_id": str(uuid4()),
            "membership_id": str(uuid4()),
            "kind": "expense",
            "amount_minor": 101,
            "occurred_at": "2026-10-12T15:00:00Z",
            "allow_completed": False,
        },
    }
    valid("Command", command)
    for field, value in [
        ("amount_minor", True),
        ("amount_minor", 1.5),
        ("amount_minor", 0),
        ("occurred_at", "tomorrow"),
        ("occurred_at", "2026-10-12T15:00:00"),
        ("membership_id", "former"),
    ]:
        invalid = deepcopy(command)
        invalid["payload"][field] = value
        valid("Command", invalid, False)
    invalid = deepcopy(command)
    del invalid["payload"]["membership_id"]
    valid("Command", invalid, False)
    recovery = deepcopy(command)
    recovery["payload"]["original_occurred_at"] = "2026-10-11T15:00:00Z"
    valid("Command", recovery, False)
    recovery["payload"]["recovery_of_command_id"] = str(uuid4())
    valid("Command", recovery)
    cabinet = {"command_id": command_id, "name": "Family", "timezone": "Asia/Shanghai"}
    valid("CreateCabinetRequest", cabinet)
    valid("CreateCabinetRequest", {**cabinet, "timezone": "Europe/Moscow"}, False)

    deletion = {"command_id": command_id, "confirm_shared_history_changes": True}
    valid("DeleteAccountRequest", deletion)
    for value in (False, "true", 1, None):
        valid("DeleteAccountRequest", {**deletion, "confirm_shared_history_changes": value}, False)
    valid("DeleteAccountRequest", {"command_id": command_id}, False)
    valid("DeleteAccountRequest", {**deletion, "command_id": "invalid"}, False)
    accepted = {
        "job_id": str(uuid4()),
        "status": "accepted",
        "primary_deletion_deadline": "2026-10-20T08:00:00Z",
        "backup_retention_days": 30,
    }
    valid("DeletionAccepted", accepted)
    valid("DeletionAccepted", {**accepted, "status": "done"}, False)
    valid("DeletionAccepted", {**accepted, "backup_retention_days": 31}, False)
    valid("DeletionAccepted", {**accepted, "primary_deletion_deadline": "later"}, False)
    valid("SignedAppleNotification", {"payload": "signed-notification-placeholder"})
    valid("SignedAppleNotification", {"signedPayload": "signed-notification-placeholder"})
    valid("SignedAppleNotification", {}, False)
    valid("SignedAppleNotification", {"payload": "a", "signedPayload": "b"}, False)
    valid("SignedAppleNotification", {"payload": 123}, False)

    # Route registration must not silently drift from the reviewed wire contract.
    from can_api.app import app

    methods = {"get", "post", "put", "patch", "delete"}
    expected_routes = {
        (path, method)
        for path, item in document["paths"].items()
        for method in item
        if method in methods
    }
    actual_routes = {
        (path, method)
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method in methods
    }
    assert actual_routes == expected_routes, (
        f"Route drift: missing={expected_routes - actual_routes}; "
        f"extra={actual_routes - expected_routes}"
    )

    print(
        f"Implementation contract: {len(schemas)} schemas, {references} references; "
        f"{len(actual_routes)} API operations; strict dates, membership epochs, "
        "recovery, receipt, deletion and Apple notification cases PASS"
    )


if __name__ == "__main__":
    main()
