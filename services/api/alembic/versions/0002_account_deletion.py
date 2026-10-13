"""Preserve the ledger while erasing account identity with durable restore tombstones."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE identity_tokens (
        id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash bytea, token_type text NOT NULL CHECK(token_type IN ('refresh_token','access_token')),
        ciphertext bytea NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(user_id,token_hash))""")
    op.execute("""INSERT INTO identity_tokens(id,user_id,token_type,ciphertext)
        SELECT user_id,user_id,'refresh_token',apple_refresh_ciphertext FROM identity_credentials""")
    op.execute("ALTER TABLE entries DROP CONSTRAINT entries_membership_id_fkey")
    op.execute(
        "ALTER TABLE entries ADD CONSTRAINT entries_membership_id_fkey FOREIGN KEY(membership_id) REFERENCES memberships(id) ON DELETE SET NULL"
    )
    for definition in (
        "command_id uuid UNIQUE",
        "reauth_proof_hash bytea",
        "accepted_json jsonb",
        "apple_tokens_ciphertext bytea",
        "apple_tokens_unavailable boolean NOT NULL DEFAULT false",
        "data_deleted_at timestamptz",
        "apple_revocation_state text NOT NULL DEFAULT 'pending' CHECK(apple_revocation_state IN ('pending','done','not_available'))",
        "journal_acknowledged_at timestamptz",
        "attempts integer NOT NULL DEFAULT 0",
        "next_attempt_at timestamptz NOT NULL DEFAULT now()",
        "lease_expires_at timestamptz",
        "lease_token uuid",
    ):
        op.execute("ALTER TABLE deletion_jobs ADD COLUMN " + definition)
    op.execute(
        "CREATE UNIQUE INDEX one_deletion_per_user ON deletion_jobs(user_id) WHERE user_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX deletion_due ON deletion_jobs(next_attempt_at) WHERE status IN ('pending','processing')"
    )


def downgrade() -> None:
    raise RuntimeError(
        "Deletion tombstones and financial history cannot be destructively downgraded"
    )
