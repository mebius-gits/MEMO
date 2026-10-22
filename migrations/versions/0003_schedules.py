"""Scheduled jobs (reminders, recurring tasks, follow-ups) and per-device settings."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("message", sa.String(120), nullable=False),
        sa.Column("minutes", sa.Integer()),
        sa.Column("time_of_day", sa.String(5)),
        sa.Column("baseline", sa.JSON()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("next_run_at", sa.DateTime()),
        sa.Column("runs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("session_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scheduled_jobs_device_id", "scheduled_jobs", ["device_id"])
    op.create_index("ix_scheduled_jobs_status", "scheduled_jobs", ["status"])
    op.create_index("ix_scheduled_jobs_next_run_at", "scheduled_jobs", ["next_run_at"])
    op.create_table(
        "device_settings",
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), primary_key=True),
        sa.Column("personality", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("device_settings")
    op.drop_table("scheduled_jobs")
