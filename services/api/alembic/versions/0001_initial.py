"""Create the Can v1.1 ledger, identity and durable queues."""

from pathlib import Path

from alembic import context, op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    source = Path(__file__).with_name("initial_schema.sql").read_text()
    source = source.replace("BEGIN;", "").replace("COMMIT;", "")
    if context.is_offline_mode():
        op.execute(source)
    else:
        op.get_bind().exec_driver_sql(source)
    op.execute(
        "ALTER TABLE cabinets ADD CONSTRAINT fixed_timezone CHECK (timezone='Asia/Shanghai')"
    )
    op.execute("ALTER TABLE entries ADD COLUMN membership_id uuid REFERENCES memberships(id)")
    op.execute("ALTER TABLE entries ADD COLUMN recovery_of_command_id uuid")
    op.execute("ALTER TABLE entries ADD COLUMN original_occurred_at timestamptz")
    op.execute("ALTER TABLE command_receipts ADD COLUMN command_type text")
    op.execute("ALTER TABLE command_receipts ADD COLUMN request_json jsonb")
    op.execute("ALTER TABLE command_receipts ADD COLUMN error_status integer")
    op.execute("ALTER TABLE push_outbox ADD COLUMN lease_token uuid")
    op.execute(
        "CREATE TABLE apple_notifications (event_id text PRIMARY KEY,received_at timestamptz NOT NULL DEFAULT now())"
    )
    op.execute(
        "CREATE TABLE request_limits (key text PRIMARY KEY, window_start timestamptz NOT NULL, count integer NOT NULL)"
    )
    op.execute(
        "CREATE TABLE worker_health (id integer PRIMARY KEY, last_seen_at timestamptz NOT NULL)"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive ledger downgrade is disabled; restore or forward-fix with owner approval"
    )
