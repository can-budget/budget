"""Activate full same-day topups without replaying historical days under new rules."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sprints DROP CONSTRAINT sprints_calculation_version_check")
    op.execute(
        "ALTER TABLE sprints ADD CONSTRAINT sprints_calculation_version_check CHECK (calculation_version IN (2,3))"
    )
    op.execute("ALTER TABLE sprints ADD COLUMN topups_today_from date")
    op.execute(
        "ALTER TABLE sprints ADD CONSTRAINT topups_today_policy CHECK ((calculation_version=2 AND topups_today_from IS NULL) OR (calculation_version=3 AND topups_today_from IS NOT NULL AND topups_today_from >= start_date AND topups_today_from <= end_date))"
    )
    op.execute("""WITH upgraded AS (
        UPDATE sprints SET calculation_version=3,
            topups_today_from=GREATEST(start_date,(now() AT TIME ZONE 'Asia/Shanghai')::date)
        WHERE closed_at IS NULL AND end_date >= (now() AT TIME ZONE 'Asia/Shanghai')::date
          AND cabinet_id IN (SELECT id FROM cabinets WHERE archived_at IS NULL)
        RETURNING cabinet_id
    ) UPDATE cabinets SET revision=revision+1 WHERE id IN (SELECT cabinet_id FROM upgraded)""")


def downgrade() -> None:
    raise RuntimeError("Financial-policy downgrade requires a reviewed forward migration")
