"""Add private password credentials without changing existing accounts or budgets."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN apple_subject DROP NOT NULL")
    op.execute("""CREATE TABLE local_credentials (
        user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        login text NOT NULL UNIQUE CHECK (login ~ '^[a-z0-9][a-z0-9_.-]{2,39}$'),
        password_hash text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )""")


def downgrade() -> None:
    raise RuntimeError("Removing local accounts requires a reviewed forward migration")
