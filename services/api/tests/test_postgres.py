from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from can_api.app import app
from can_api.db import execute, one
from can_api.ledger import business_date
from can_api.security import issue_session, now

pytestmark = pytest.mark.postgres


def seed_user(engine):
    user_id = uuid4()
    with engine.begin() as c:
        execute(
            c,
            "INSERT INTO users(id,apple_subject,display_name) VALUES (:id,:subject,'Tester')",
            id=user_id,
            subject=str(user_id),
        )
        tokens = issue_session(c, user_id)
    return {"Authorization": "Bearer " + tokens["access_token"]}, user_id, tokens


def setup_budget(engine):
    headers, user, tokens = seed_user(engine)
    client = TestClient(app)
    cabinet = client.post(
        "/v1/cabinets",
        headers=headers,
        json={"command_id": str(uuid4()), "name": "Test budget", "timezone": "Asia/Shanghai"},
    )
    assert cabinet.status_code == 201, cabinet.text
    cabinet_id = cabinet.json()["cabinet_id"]
    membership = client.get("/v1/me", headers=headers).json()["active_membership_id"]
    sprint_id = str(uuid4())
    create = {
        "command_id": str(uuid4()),
        "type": "create_sprint",
        "payload": {
            "sprint_id": sprint_id,
            "initial_minor": 1_000_000,
            "end_date": str(business_date() + timedelta(days=9)),
            "expected_cabinet_revision": 1,
        },
    }
    assert (
        client.post(f"/v1/cabinets/{cabinet_id}/commands", headers=headers, json=create).status_code
        == 200
    )
    return client, headers, cabinet_id, sprint_id, membership


def purchase(sprint, membership, amount=100):
    return {
        "command_id": str(uuid4()),
        "type": "add_entry",
        "payload": {
            "entry_id": str(uuid4()),
            "sprint_id": sprint,
            "membership_id": membership,
            "kind": "expense",
            "amount_minor": amount,
            "occurred_at": now().isoformat(),
            "allow_completed": False,
        },
    }


def restart_budget(sprint, revision=2, initial=1_100_000, days=30):
    return {
        "command_id": str(uuid4()),
        "type": "create_sprint",
        "payload": {
            "sprint_id": str(uuid4()),
            "initial_minor": initial,
            "end_date": str(business_date() + timedelta(days=days - 1)),
            "expected_cabinet_revision": revision,
            "expected_business_date": str(business_date()),
            "replaces_sprint_id": sprint,
        },
    }


def test_new_budget_starts_fresh_and_preserves_old_budget_history(postgres, monkeypatch):
    from can_api import ledger

    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    path = f"/v1/cabinets/{cabinet}/commands"
    # Give the previous budget a carry from yesterday, then today's real entries.
    with postgres.begin() as c:
        execute(
            c,
            "UPDATE sprints SET start_date=:yesterday WHERE id=:id",
            yesterday=business_date() - timedelta(days=1),
            id=sprint,
        )
    carry = {
        "command_id": str(uuid4()),
        "type": "choose_carry",
        "payload": {
            "sprint_id": sprint,
            "day": str(business_date()),
            "mode": "today",
            "expected_cabinet_revision": 2,
        },
    }
    assert client.post(path, headers=headers, json=carry).status_code == 200
    expense = purchase(sprint, membership, 15400)
    topup = purchase(sprint, membership, 2000)
    topup["payload"]["kind"] = "topup"
    for entry in (expense, topup):
        assert client.post(path, headers=headers, json=entry).status_code == 200
    old = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert old["projection"]["carry_status"] == "selected_today"
    command = restart_budget(sprint, revision=old["cabinet_revision"])
    first = client.post(path, headers=headers, json=command)
    assert first.status_code == 200, first.text
    assert client.post(path, headers=headers, json=command).json() == first.json()
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    new_id = command["payload"]["sprint_id"]
    assert state["sprint"]["id"] == new_id
    assert state["sprint"]["start_date"] == str(business_date())
    assert state["sprint"]["closed_at"] is None
    assert state["projection"]["bank_minor"] == 1_100_000
    assert state["projection"]["today_remaining_minor"] == 36666
    assert state["projection"]["days_remaining"] == 30
    assert state["projection"]["carry_status"] == "none"
    assert state["daily_totals"] == state["carry_decisions"] == state["sprint_revisions"] == []
    assert state["cabinet_revision"] == old["cabinet_revision"] + 1
    archive_path = f"/v1/cabinets/{cabinet}/sprints/{sprint}/projection"
    archived = client.get(archive_path, headers=headers).json()
    assert archived["sprint"]["status"] == archived["projection"]["status"] == "completed"
    assert archived["sprint"]["closed_at"] is not None
    assert archived["sprint"]["end_date"] == old["sprint"]["end_date"]
    assert archived["projection"]["bank_minor"] == old["projection"]["bank_minor"]
    assert archived["projection"]["today_remaining_minor"] is None
    assert archived["projection"]["days_remaining"] == 0
    assert archived["daily_totals"] == old["daily_totals"]
    assert archived["carry_decisions"] == old["carry_decisions"]
    items = client.get(f"/v1/cabinets/{cabinet}/sprints/{sprint}/entries", headers=headers).json()[
        "items"
    ]
    assert {entry["id"] for entry in items} == {
        expense["payload"]["entry_id"],
        topup["payload"]["entry_id"],
    }
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS count FROM sprints")["count"] == 2
        assert one(c, "SELECT count(*) AS count FROM sprints WHERE closed_at IS NULL")["count"] == 1
    # Advancing the day must not resume the archived budget or recalculate its carry.
    future = business_date() + timedelta(days=5)
    monkeypatch.setattr(ledger, "business_date", lambda: future)
    assert client.get(archive_path, headers=headers).json()["projection"] == archived["projection"]


@pytest.mark.parametrize(
    "failure", ["same_id", "wrong_previous", "past_end", "changed_day", "no_replacement"]
)
def test_failed_restart_keeps_existing_budget_active(postgres, failure):
    client, headers, cabinet, sprint, _ = setup_budget(postgres)
    command = restart_budget(sprint)
    expected = {
        "same_id": "sprint_id_conflict",
        "wrong_previous": "stale_revision",
        "past_end": "invalid_end_date",
        "changed_day": "business_date_changed",
        "no_replacement": "sprint_already_active",
    }[failure]
    if failure == "same_id":
        command["payload"]["sprint_id"] = sprint
    if failure == "wrong_previous":
        command["payload"]["replaces_sprint_id"] = str(uuid4())
    if failure == "past_end":
        command["payload"]["end_date"] = str(business_date() - timedelta(days=1))
    if failure == "changed_day":
        command["payload"]["expected_business_date"] = str(business_date() - timedelta(days=1))
    if failure == "no_replacement":
        del command["payload"]["replaces_sprint_id"]
    response = client.post(f"/v1/cabinets/{cabinet}/commands", headers=headers, json=command)
    assert response.status_code in (409, 422) and response.json()["code"] == expected
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert state["sprint"]["id"] == sprint and state["sprint"]["closed_at"] is None
    assert state["cabinet_revision"] == 2 and state["projection"]["bank_minor"] == 1_000_000
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS count FROM sprints")["count"] == 1


def test_concurrent_restarts_create_only_one_new_budget(postgres):
    client, headers, cabinet, sprint, _ = setup_budget(postgres)
    commands = [restart_budget(sprint) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda command: client.post(
                    f"/v1/cabinets/{cabinet}/commands", headers=headers, json=command
                ),
                commands,
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert (
        next(response for response in responses if response.status_code == 409).json()["code"]
        == "stale_revision"
    )
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS count FROM sprints")["count"] == 2
        assert one(c, "SELECT count(*) AS count FROM sprints WHERE closed_at IS NULL")["count"] == 1


def test_old_offline_entries_require_confirmation_and_never_enter_new_budget(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    path = f"/v1/cabinets/{cabinet}/commands"
    late = purchase(sprint, membership, 100)
    command = restart_budget(sprint)
    assert client.post(path, headers=headers, json=command).status_code == 200
    refused = client.post(path, headers=headers, json=late)
    assert (
        refused.status_code == 409 and refused.json()["code"] == "completed_confirmation_required"
    )
    recovered = {
        **late,
        "command_id": str(uuid4()),
        "payload": {
            **late["payload"],
            "allow_completed": True,
            "recovery_of_command_id": late["command_id"],
        },
    }
    assert client.post(path, headers=headers, json=recovered).status_code == 200
    after_close = purchase(sprint, membership, 100)
    after_close["payload"].update(
        occurred_at=(now() + timedelta(seconds=1)).isoformat(), allow_completed=True
    )
    response = client.post(path, headers=headers, json=after_close)
    assert response.status_code == 422 and response.json()["code"] == "entry_outside_sprint"
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert state["sprint"]["id"] == command["payload"]["sprint_id"]
    assert state["projection"]["bank_minor"] == 1_100_000
    assert state["projection"]["today_remaining_minor"] == 36666
    assert state["daily_totals"] == []
    archived = client.get(
        f"/v1/cabinets/{cabinet}/sprints/{sprint}/projection", headers=headers
    ).json()
    assert archived["projection"]["bank_minor"] == 999900
    update = {
        "command_id": str(uuid4()),
        "type": "update_sprint",
        "payload": {
            "sprint_id": sprint,
            "expected_cabinet_revision": state["cabinet_revision"],
            "expected_business_date": str(business_date()),
            "bank_minor": 100,
        },
    }
    assert client.post(path, headers=headers, json=update).json()["code"] == "sprint_completed"
    # With a clean 30-day budget, a one-ruble daily overspend changes future days by kopeks.
    spent = purchase(command["payload"]["sprint_id"], membership, 36766)
    assert client.post(path, headers=headers, json=spent).status_code == 200
    p = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()["projection"]
    assert p["today_remaining_minor"] == -100
    assert p["bank_minor"] // (p["days_remaining"] - 1) == 36663


def test_lost_response_replay_snapshot_and_payload_collision(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    body = purchase(sprint, membership, 120_000)
    path = f"/v1/cabinets/{cabinet}/commands"
    results = [client.post(path, headers=headers, json=body) for _ in range(20)]
    assert all(result.status_code == 200 for result in results)
    assert all(result.json() == results[0].json() for result in results)
    state = client.post(
        f"/v1/cabinets/{cabinet}/sync",
        headers=headers,
        json={"unresolved_command_ids": [body["command_id"]]},
    ).json()
    assert state["snapshot"]["projection"]["bank_minor"] == 880_000
    assert state["receipts"][0]["status"] == "applied"
    assert state["receipts"][0]["result_revision"] == state["snapshot"]["cabinet_revision"]
    body["payload"]["amount_minor"] += 1
    assert (
        client.post(path, headers=headers, json=body).json()["code"] == "command_payload_conflict"
    )
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS value FROM entries")["value"] == 1


def test_concurrent_purchases_and_edit_conflict(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    path = f"/v1/cabinets/{cabinet}/commands"
    bodies = [purchase(sprint, membership, 100) for _ in range(10)]
    with ThreadPoolExecutor(max_workers=10) as executor:
        responses = list(
            executor.map(lambda body: client.post(path, headers=headers, json=body), bodies)
        )
    assert all(response.status_code == 200 for response in responses)
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert state["projection"]["bank_minor"] == 999_000
    edit = {
        "command_id": str(uuid4()),
        "type": "update_sprint",
        "payload": {
            "sprint_id": sprint,
            "expected_cabinet_revision": 2,
            "expected_business_date": str(business_date()),
            "bank_minor": 900_000,
        },
    }
    response = client.post(path, headers=headers, json=edit)
    assert response.status_code == 409 and response.json()["code"] == "stale_revision"
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS value FROM sprint_revisions")["value"] == 0


def test_bank_target_fixed_delta_noop_and_late_entry(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    path = f"/v1/cabinets/{cabinet}/commands"
    assert (
        client.post(path, headers=headers, json=purchase(sprint, membership, 120_000)).status_code
        == 200
    )
    edit = {
        "command_id": str(uuid4()),
        "type": "update_sprint",
        "payload": {
            "sprint_id": sprint,
            "expected_cabinet_revision": 3,
            "expected_business_date": str(business_date()),
            "bank_minor": 900_000,
        },
    }
    assert client.post(path, headers=headers, json=edit).status_code == 200
    assert client.post(path, headers=headers, json=edit).status_code == 200
    noop = {
        **edit,
        "command_id": str(uuid4()),
        "payload": {**edit["payload"], "expected_cabinet_revision": 4},
    }
    assert client.post(path, headers=headers, json=noop).json()["result_revision"] == 4
    assert (
        client.post(path, headers=headers, json=purchase(sprint, membership, 10_000)).status_code
        == 200
    )
    assert (
        client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()["projection"][
            "bank_minor"
        ]
        == 890_000
    )
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS value FROM sprint_revisions")["value"] == 1
        assert (
            one(c, "SELECT bank_adjustment_minor FROM sprint_revisions")["bank_adjustment_minor"]
            == 20_000
        )


def test_bank_limit_exact_zero_and_rejected_receipt_replay(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    path = f"/v1/cabinets/{cabinet}/commands"
    body = purchase(sprint, membership, 1_000_001)
    first = client.post(path, headers=headers, json=body)
    assert first.status_code == 409 and first.json()["code"] == "insufficient_funds"
    # A later topup cannot turn a rejected immutable command into an applied command.
    topup = purchase(sprint, membership, 100)
    topup["payload"]["kind"] = "topup"
    assert client.post(path, headers=headers, json=topup).status_code == 200
    assert client.post(path, headers=headers, json=body).json() == first.json()
    exact = purchase(sprint, membership, 1_000_100)
    assert client.post(path, headers=headers, json=exact).status_code == 200
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert state["projection"]["bank_minor"] == 0
    revision = state["cabinet_revision"]
    excess = client.post(path, headers=headers, json=purchase(sprint, membership, 1))
    assert excess.status_code == 409 and excess.json()["code"] == "insufficient_funds"
    unchanged = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert unchanged["projection"] == state["projection"]
    assert unchanged["cabinet_revision"] == revision
    sync = client.post(
        f"/v1/cabinets/{cabinet}/sync",
        headers=headers,
        json={"unresolved_command_ids": [body["command_id"]]},
    ).json()
    assert sync["receipts"][0]["status"] == "rejected"
    assert sync["receipts"][0]["error"]["code"] == "insufficient_funds"
    assert sync["snapshot"]["cabinet_revision"] == revision
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS count FROM entries")["count"] == 2
        assert one(c, "SELECT count(*) AS count FROM entry_revisions")["count"] == 2


def test_concurrent_expenses_cannot_overdraw_the_bank(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    path = f"/v1/cabinets/{cabinet}/commands"
    bodies = [purchase(sprint, membership, 600_000) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(lambda body: client.post(path, headers=headers, json=body), bodies)
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["code"] == "insufficient_funds"
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert state["projection"]["bank_minor"] == 400_000
    assert state["cabinet_revision"] == 3


@pytest.mark.parametrize(
    "operation", ["void_topup", "reduce_topup", "increase_expense", "topup_to_expense"]
)
def test_history_changes_cannot_overdraw_the_bank(postgres, operation):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    path = f"/v1/cabinets/{cabinet}/commands"
    topup = purchase(sprint, membership, 100_000)
    topup["payload"]["kind"] = "topup"
    expense = purchase(sprint, membership, 1_050_000)
    assert client.post(path, headers=headers, json=topup).status_code == 200
    assert client.post(path, headers=headers, json=expense).status_code == 200
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    entry = expense if operation == "increase_expense" else topup
    payload = {
        "entry_id": entry["payload"]["entry_id"],
        "sprint_id": sprint,
        "expected_entry_revision": 1,
        "allow_completed": False,
    }
    kind = "void_entry" if operation == "void_topup" else "correct_entry"
    if kind == "correct_entry":
        payload.update(
            kind="expense" if operation in {"increase_expense", "topup_to_expense"} else "topup",
            amount_minor=1_100_001 if operation == "increase_expense" else 1,
        )
    response = client.post(
        path, headers=headers, json={"command_id": str(uuid4()), "type": kind, "payload": payload}
    )
    assert response.status_code == 409 and response.json()["code"] == "insufficient_funds"
    current = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert current["cabinet_revision"] == state["cabinet_revision"]
    assert current["projection"] == state["projection"]
    with postgres.connect() as c:
        row = one(
            c,
            "SELECT revision,amount_minor,deleted_at FROM entries WHERE id=:id",
            id=entry["payload"]["entry_id"],
        )
        assert row["revision"] == 1 and row["amount_minor"] == entry["payload"]["amount_minor"]
        assert row["deleted_at"] is None


def test_bank_settings_reject_negative_target(postgres):
    client, headers, cabinet, sprint, _ = setup_budget(postgres)
    command = {
        "command_id": str(uuid4()),
        "type": "update_sprint",
        "payload": {
            "sprint_id": sprint,
            "expected_cabinet_revision": 2,
            "expected_business_date": str(business_date()),
            "bank_minor": -1,
        },
    }
    response = client.post(f"/v1/cabinets/{cabinet}/commands", headers=headers, json=command)
    assert response.status_code == 409 and response.json()["code"] == "insufficient_funds"
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert state["projection"]["bank_minor"] == 1_000_000 and state["cabinet_revision"] == 2


def test_existing_historical_debt_can_be_reduced_but_not_increased(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    path = f"/v1/cabinets/{cabinet}/commands"
    legacy = purchase(sprint, membership, 1_000_000)
    assert client.post(path, headers=headers, json=legacy).status_code == 200
    # Simulate debt recorded by an older release; the new guard does not rewrite history.
    with postgres.begin() as c:
        execute(
            c,
            "UPDATE entries SET amount_minor=1_100_000 WHERE id=:id",
            id=legacy["payload"]["entry_id"],
        )
    expense = client.post(path, headers=headers, json=purchase(sprint, membership, 1))
    assert expense.status_code == 409 and expense.json()["code"] == "insufficient_funds"
    topup = purchase(sprint, membership, 1)
    topup["payload"]["kind"] = "topup"
    assert client.post(path, headers=headers, json=topup).status_code == 200
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert state["projection"]["bank_minor"] == -99_999


def test_leave_replay_denies_financial_reads_and_widgets(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    device = str(uuid4())
    response = client.post(
        "/v1/devices",
        headers=headers,
        json={
            "device_id": device,
            "app_apns_token": None,
            "widget_push_token": None,
            "apns_environment": "production",
            "app_version": "1",
            "os_version": "17",
        },
    )
    assert response.status_code == 204
    widget = client.post(f"/v1/devices/{device}/widget-token", headers=headers).json()["token"]
    leave = {
        "command_id": str(uuid4()),
        "type": "leave_cabinet",
        "payload": {"expected_cabinet_revision": 2},
    }
    path = f"/v1/cabinets/{cabinet}/commands"
    first = client.post(path, headers=headers, json=leave)
    assert first.status_code == 200
    assert client.post(path, headers=headers, json=leave).json() == first.json()
    receipt = client.get(f"/v1/commands/{leave['command_id']}/receipt", headers=headers)
    assert receipt.status_code == 200 and receipt.json()["status"] == "applied"
    assert "snapshot" not in receipt.json()
    unknown = client.get(f"/v1/commands/{uuid4()}/receipt", headers=headers)
    assert unknown.json()["status"] == "unknown"
    assert client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).status_code == 403
    assert (
        client.get("/v1/widget/snapshot", headers={"Authorization": "Bearer " + widget}).status_code
        == 401
    )
    assert client.post(path, headers=headers, json=purchase(sprint, membership)).status_code == 403


def test_two_joiners_compete_for_tenth_slot(postgres):
    client, headers, cabinet, _, _ = setup_budget(postgres)
    with postgres.begin() as c:
        for _ in range(8):
            user = uuid4()
            execute(
                c,
                "INSERT INTO users(id,apple_subject,display_name) VALUES (:id,:subject,'Tester')",
                id=user,
                subject=str(user),
            )
            execute(
                c,
                "INSERT INTO memberships(id,cabinet_id,user_id) VALUES (:id,:cabinet,:user)",
                id=uuid4(),
                cabinet=cabinet,
                user=user,
            )
    codes = []
    for _ in range(2):
        invite = {"command_id": str(uuid4()), "type": "create_invitation", "payload": {}}
        codes.append(
            client.post(f"/v1/cabinets/{cabinet}/commands", headers=headers, json=invite).json()[
                "invitation"
            ]["code"]
        )
    identities = [seed_user(postgres)[0] for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(
            executor.map(
                lambda item: client.post(
                    "/v1/invitations/accept",
                    headers=item[0],
                    json={"command_id": str(uuid4()), "code": item[1]},
                ),
                zip(identities, codes),
            )
        )
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert [response.json()["code"] for response in responses if response.status_code == 409] == [
        "cabinet_full"
    ]
    with postgres.connect() as c:
        assert (
            one(
                c,
                "SELECT count(*) AS value FROM memberships WHERE cabinet_id=:id AND left_at IS NULL",
                id=cabinet,
            )["value"]
            == 10
        )


def test_refresh_loss_reuse_and_deletion_reauth_guard(postgres):
    headers, _, session = seed_user(postgres)
    client = TestClient(app)
    body = {"refresh_token": session["refresh_token"], "refresh_request_id": str(uuid4())}
    first = client.post("/v1/auth/refresh", json=body)
    assert first.status_code == 200
    assert client.post("/v1/auth/refresh", json=body).json() == first.json()
    new_headers = {"Authorization": "Bearer " + first.json()["access_token"]}
    assert (
        client.request(
            "DELETE",
            "/v1/me",
            headers=new_headers,
            json={"command_id": str(uuid4()), "confirm_shared_history_changes": True},
        ).status_code
        == 422
    )
    body["refresh_request_id"] = str(uuid4())
    assert client.post("/v1/auth/refresh", json=body).status_code == 401
    assert client.get("/v1/me", headers=new_headers).status_code == 401


def add_member(client, postgres, headers, cabinet):
    invitation = client.post(
        f"/v1/cabinets/{cabinet}/commands",
        headers=headers,
        json={"command_id": str(uuid4()), "type": "create_invitation", "payload": {}},
    )
    assert invitation.status_code == 200
    other, _, _ = seed_user(postgres)
    response = client.post(
        "/v1/invitations/accept",
        headers=other,
        json={"command_id": str(uuid4()), "code": invitation.json()["invitation"]["code"]},
    )
    assert response.status_code == 200
    return other


def test_carry_first_writer_and_stale_preview(postgres):
    client, headers, cabinet, sprint, _ = setup_budget(postgres)
    other = add_member(client, postgres, headers, cabinet)
    with postgres.begin() as c:
        execute(
            c,
            "UPDATE sprints SET start_date=:start,initial_end_date=:end,end_date=:end WHERE id=:id",
            start=business_date() - timedelta(days=1),
            end=business_date() + timedelta(days=8),
            id=sprint,
        )
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert state["projection"]["carry_status"] == "pending"
    commands = [
        {
            "command_id": str(uuid4()),
            "type": "choose_carry",
            "payload": {
                "sprint_id": sprint,
                "day": str(business_date()),
                "mode": mode,
                "expected_cabinet_revision": state["cabinet_revision"],
            },
        }
        for mode in ("today", "distribute")
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda pair: client.post(
                    f"/v1/cabinets/{cabinet}/commands", headers=pair[0], json=pair[1]
                ),
                zip([headers, other], commands),
            )
        )
    assert sorted(result.status_code for result in results) == [200, 409]
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS value FROM carry_decisions")["value"] == 1


def test_rejoining_cannot_reanimate_old_unknown_commands(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    other = add_member(client, postgres, headers, cabinet)
    accepted = purchase(sprint, membership)
    path = f"/v1/cabinets/{cabinet}/commands"
    original = client.post(path, headers=headers, json=accepted)
    assert original.status_code == 200
    unknown = purchase(sprint, membership)
    state = client.get(f"/v1/cabinets/{cabinet}/snapshot", headers=headers).json()
    assert (
        client.post(
            path,
            headers=headers,
            json={
                "command_id": str(uuid4()),
                "type": "leave_cabinet",
                "payload": {"expected_cabinet_revision": state["cabinet_revision"]},
            },
        ).status_code
        == 200
    )
    invitation = client.post(
        path,
        headers=other,
        json={"command_id": str(uuid4()), "type": "create_invitation", "payload": {}},
    )
    assert (
        client.post(
            "/v1/invitations/accept",
            headers=headers,
            json={"command_id": str(uuid4()), "code": invitation.json()["invitation"]["code"]},
        ).status_code
        == 200
    )
    assert client.get("/v1/me", headers=headers).json()["active_membership_id"] != membership
    assert client.post(path, headers=headers, json=accepted).json() == original.json()
    rejected = client.post(path, headers=headers, json=unknown)
    assert rejected.status_code == 409 and rejected.json()["code"] == "membership_epoch_mismatch"


def test_clock_recovery_preserves_entry_identity_and_audit(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    original = purchase(sprint, membership)
    original["payload"]["occurred_at"] = (now() + timedelta(minutes=15)).isoformat()
    path = f"/v1/cabinets/{cabinet}/commands"
    rejected = client.post(path, headers=headers, json=original)
    assert rejected.status_code == 422 and rejected.json()["code"] == "clock_skew"
    recovered = {
        "command_id": str(uuid4()),
        "type": "add_entry",
        "payload": {
            **original["payload"],
            "occurred_at": now().isoformat(),
            "recovery_of_command_id": original["command_id"],
            "original_occurred_at": original["payload"]["occurred_at"],
        },
    }
    response = client.post(path, headers=headers, json=recovered)
    assert response.status_code == 200, response.text
    assert response.json()["entity_id"] == original["payload"]["entry_id"]
    assert client.post(path, headers=headers, json=recovered).json() == response.json()
    with postgres.connect() as c:
        record = one(c, "SELECT * FROM entries WHERE id=:id", id=original["payload"]["entry_id"])
        assert str(record["recovery_of_command_id"]) == original["command_id"]
        assert record["original_occurred_at"] > record["occurred_at"]
        assert one(c, "SELECT count(*) AS value FROM entries")["value"] == 1


def test_completed_recovery_requires_explicit_confirmation(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    yesterday = business_date() - timedelta(days=1)
    with postgres.begin() as c:
        execute(
            c,
            "UPDATE memberships SET joined_at=joined_at-interval '2 days' WHERE id=:id",
            id=membership,
        )
        execute(
            c,
            "UPDATE sprints SET start_date=:day,initial_end_date=:day,end_date=:day,topups_today_from=:day WHERE id=:id",
            day=yesterday,
            id=sprint,
        )
    original = purchase(sprint, membership)
    original["payload"]["occurred_at"] = (now() - timedelta(days=1)).isoformat()
    path = f"/v1/cabinets/{cabinet}/commands"
    assert (
        client.post(path, headers=headers, json=original).json()["code"]
        == "completed_confirmation_required"
    )
    recovered = {
        "command_id": str(uuid4()),
        "type": "add_entry",
        "payload": {
            **original["payload"],
            "allow_completed": True,
            "recovery_of_command_id": original["command_id"],
        },
    }
    assert client.post(path, headers=headers, json=recovered).status_code == 200
    result = client.get(
        f"/v1/cabinets/{cabinet}/sprints/{sprint}/projection", headers=headers
    ).json()
    assert result["projection"]["bank_minor"] == 999_900
    assert result["projection"]["status"] == "completed"


def test_full_today_topup_survives_receipt_replay_and_correction(postgres):
    client, headers, cabinet, sprint, membership = setup_budget(postgres)
    body = purchase(sprint, membership, 2000)
    body["payload"]["kind"] = "topup"
    path = f"/v1/cabinets/{cabinet}/commands"
    first = client.post(path, headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert client.post(path, headers=headers, json=body).json() == first.json()
    sync = client.post(
        f"/v1/cabinets/{cabinet}/sync",
        headers=headers,
        json={"unresolved_command_ids": [body["command_id"]]},
    ).json()
    state = sync["snapshot"]
    assert state["sprint"]["calculation_version"] == 3
    assert state["sprint"]["topups_today_from"] == str(business_date())
    assert state["projection"]["today_remaining_minor"] == 102000
    assert state["projection"]["bank_minor"] == 1002000
    assert sync["receipts"][0]["status"] == "applied"
    with postgres.connect() as c:
        assert one(c, "SELECT count(*) AS count FROM entries")["count"] == 1


def test_policy_migration_preserves_past_days_closed_sprints_and_entry_amounts(postgres):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from can_api.ledger import projection_data

    today = business_date()
    yesterday = today - timedelta(days=1)
    cabinet, active, closed = uuid4(), uuid4(), uuid4()
    migration = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with postgres.begin() as c:
        # Recreate the pre-upgrade shape only inside the fixture's disposable schema.
        execute(c, "ALTER TABLE sprints DROP CONSTRAINT topups_today_policy")
        execute(c, "ALTER TABLE sprints DROP COLUMN topups_today_from")
        execute(c, "ALTER TABLE sprints DROP CONSTRAINT sprints_calculation_version_check")
        execute(
            c,
            "ALTER TABLE sprints ADD CONSTRAINT sprints_calculation_version_check CHECK (calculation_version=2)",
        )
        execute(c, "UPDATE alembic_version SET version_num='0002'")
        execute(
            c,
            "INSERT INTO cabinets(id,name,timezone,revision) VALUES (:id,'Migration','Asia/Shanghai',7)",
            id=cabinet,
        )
        for sprint, start, end, finished in [
            (active, yesterday, today + timedelta(days=8), False),
            (closed, today - timedelta(days=20), today - timedelta(days=11), True),
        ]:
            execute(
                c,
                """INSERT INTO sprints(id,cabinet_id,initial_minor,start_date,initial_end_date,end_date,closed_at)
                VALUES (:id,:cabinet,200000,:start,:end,:end,CASE WHEN :finished THEN now() ELSE NULL END)""",
                id=sprint,
                cabinet=cabinet,
                start=start,
                end=end,
                finished=finished,
            )
        for day in (yesterday, today):
            execute(
                c,
                """INSERT INTO entries(id,cabinet_id,sprint_id,kind,amount_minor,occurred_at,effective_day,created_revision,updated_revision)
                VALUES (:id,:cabinet,:sprint,'topup',2000,now(),:day,1,1)""",
                id=uuid4(),
                cabinet=cabinet,
                sprint=active,
                day=day,
            )
        before = one(c, "SELECT * FROM sprints WHERE id=:id", id=active)
        prior_day = projection_data(c, before, yesterday)["projection"]
        prior_bank = projection_data(c, before, today)["projection"]["bank_minor"]
        migration.attributes["connection"] = c
        # This fixture reverses only policy 0003, not subsequent schema migrations.
        command.upgrade(migration, "0003")
        command.upgrade(migration, "0003")
        after = one(c, "SELECT * FROM sprints WHERE id=:id", id=active)
        assert after["calculation_version"] == 3
        assert after["topups_today_from"] == today
        assert projection_data(c, after, yesterday)["projection"] == prior_day
        assert projection_data(c, after, today)["projection"]["bank_minor"] == prior_bank
        assert (
            one(c, "SELECT calculation_version FROM sprints WHERE id=:id", id=closed)[
                "calculation_version"
            ]
            == 2
        )
        assert one(c, "SELECT revision FROM cabinets WHERE id=:id", id=cabinet)["revision"] == 8
        assert one(c, "SELECT sum(amount_minor) AS total FROM entries")["total"] == 4000
