"""Durable best-effort APNs delivery; money commits never wait for push.

Apple payload reference:
https://developer.apple.com/documentation/widgetkit/updating-widgets-with-widgetkit-push-notifications
"""

import logging
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import jwt

from .config import settings
from .db import engine, execute, one, rows
from .errors import Problem, require
from .security import decrypt, now


def push_request(device: dict, widget: bool) -> tuple[str, dict, dict]:
    cfg = settings()
    require(
        cfg.apns_key_id and cfg.apns_team_id and cfg.apns_private_key, "apns_not_configured", 503
    )
    credential = jwt.encode(
        {"iss": cfg.apns_team_id, "iat": int(time.time())},
        cfg.apns_private_key,
        algorithm="ES256",
        headers={"kid": cfg.apns_key_id},
    )
    field = "widget_push_token_ciphertext" if widget else "app_apns_token_ciphertext"
    topic = cfg.apns_widget_topic if widget else cfg.apns_app_topic
    require(topic, "apns_topic_not_configured", 503)
    host = (
        "api.sandbox.push.apple.com"
        if device["apns_environment"] == "sandbox"
        else "api.push.apple.com"
    )
    headers = {
        "authorization": f"bearer {credential}",
        "apns-topic": topic,
        "apns-push-type": "widgets" if widget else "background",
        "apns-priority": "5",
        "apns-expiration": "0",
        "apns-collapse-id": "can-state-changed",
    }
    payload = {"aps": {"content-changed": True}} if widget else {"aps": {"content-available": 1}}
    return f"https://{host}/3/device/{decrypt(device[field])}", headers, payload


def cycle() -> bool:
    lease = uuid4()
    with engine().begin() as c:
        execute(
            c,
            """UPDATE invitations SET code_ciphertext=NULL WHERE code_ciphertext IS NOT NULL
            AND (expires_at<=now() OR used_at IS NOT NULL OR revoked_at IS NOT NULL)""",
        )
        execute(
            c,
            """UPDATE refresh_tokens SET encrypted_retry_response=NULL
            WHERE encrypted_retry_response IS NOT NULL AND response_expires_at<now()""",
        )
        execute(c, "DELETE FROM request_limits WHERE window_start<now()-interval '1 day'")
        execute(c, "DELETE FROM auth_challenges WHERE expires_at<now()-interval '1 day'")
        execute(c, "DELETE FROM reauth_tokens WHERE expires_at<now()-interval '1 day'")
        execute(c, "DELETE FROM apple_notifications WHERE received_at<now()-interval '2 days'")
        execute(
            c,
            """UPDATE push_outbox SET status='pending',lease_expires_at=NULL
            WHERE status='processing' AND lease_expires_at<now()""",
        )
        job = one(
            c,
            """SELECT * FROM push_outbox WHERE status='pending' AND next_attempt_at<=now()
            ORDER BY next_attempt_at,cabinet_revision DESC FOR UPDATE SKIP LOCKED LIMIT 1""",
        )
        if job:
            newer = one(
                c,
                """SELECT id FROM push_outbox WHERE cabinet_id=:cabinet
                AND cabinet_revision>:revision AND status IN ('pending','processing','done') LIMIT 1""",
                cabinet=job["cabinet_id"],
                revision=job["cabinet_revision"],
            )
            if newer:
                execute(c, "UPDATE push_outbox SET status='done' WHERE id=:id", id=job["id"])
                job = None
        if job:
            execute(
                c,
                """UPDATE push_outbox SET status='processing',attempts=attempts+1,
                lease_expires_at=now()+interval '5 minutes',lease_token=:lease WHERE id=:id""",
                id=job["id"],
                lease=lease,
            )
            devices = rows(
                c,
                """SELECT d.* FROM devices d JOIN memberships m ON m.user_id=d.user_id
                JOIN sessions s ON s.id=d.session_id WHERE m.cabinet_id=:id AND m.left_at IS NULL
                AND s.revoked_at IS NULL AND s.absolute_expires_at>now()""",
                id=job["cabinet_id"],
            )
        else:
            devices = []
        execute(
            c,
            """INSERT INTO worker_health(id,last_seen_at) VALUES (1,now())
            ON CONFLICT(id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
        )
    heartbeat()
    if not job:
        return False
    error: str | None = None
    with httpx.Client(http2=True, timeout=10) as client:
        for device in devices:
            for widget in (False, True):
                field = "widget_push_token_ciphertext" if widget else "app_apns_token_ciphertext"
                if not device[field] or (widget and not settings().apns_widget_topic):
                    continue
                try:
                    url, headers, payload = push_request(device, widget)
                    response = client.post(url, headers=headers, json=payload)
                    reason = response.json().get("reason") if response.content else None
                    if response.status_code == 410 or reason in ("BadDeviceToken", "Unregistered"):
                        with engine().begin() as c:
                            execute(
                                c,
                                f"UPDATE devices SET {field}=NULL WHERE id=:id AND {field}=:cipher",
                                id=device["id"],
                                cipher=device[field],
                            )
                    elif response.status_code != 200:
                        error = (
                            "apns_rejected" if response.status_code < 500 else "apns_unavailable"
                        )
                except (httpx.HTTPError, ValueError):
                    error = "apns_unavailable"
                except Problem as exc:
                    error = str(exc.body["code"])
                with engine().begin() as c:
                    execute(
                        c,
                        "UPDATE push_outbox SET lease_expires_at=now()+interval '5 minutes' WHERE id=:id AND lease_token=:lease",
                        id=job["id"],
                        lease=lease,
                    )
                    execute(c, "UPDATE worker_health SET last_seen_at=now() WHERE id=1")
                heartbeat()
    with engine().begin() as c:
        delay = min(3600, 2 ** min(job["attempts"] + 1, 11))
        execute(
            c,
            """UPDATE push_outbox SET status=:status,lease_expires_at=NULL,
            next_attempt_at=:next,last_error_code=:error WHERE id=:id AND lease_token=:lease""",
            status="pending" if error else "done",
            next=now() + timedelta(seconds=delay),
            error=error,
            id=job["id"],
            lease=lease,
        )
    return True


def heartbeat() -> None:
    path = Path("/tmp/can-worker-heartbeat")
    path.with_suffix(".tmp").write_text(str(int(time.time())))
    path.with_suffix(".tmp").replace(path)


def main() -> None:
    # HTTP request URL logging would disclose the APNs token in the path.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    while True:
        from .deletions import cycle as deletion_cycle

        deletion_active = False
        try:
            deletion_active = deletion_cycle()
        except Exception:
            # A journal outage must not prevent independent push delivery.
            pass
        try:
            active = cycle() or deletion_active
        except Exception:
            # No exception text: database/transport exceptions can contain secret parameters.
            active = deletion_active
        time.sleep(1 if active else 5)


if __name__ == "__main__":
    main()
