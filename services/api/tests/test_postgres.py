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
        command.upgrade(migration, "head")
        command.upgrade(migration, "head")
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
