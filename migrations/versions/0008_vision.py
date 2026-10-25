"""Look tasks: what each scheduled photo should check, the photos' records and daily counts."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scheduled_jobs", sa.Column("look", sa.JSON(), nullable=True))
    op.add_column("scheduled_jobs", sa.Column("look_state", sa.JSON(), nullable=True))
    op.create_table(
        "vision_observations",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("task_id", sa.String(64), nullable=True),
        sa.Column("run", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("question", sa.String(120), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("scene", sa.JSON(), nullable=True),
        sa.Column("quality", sa.JSON(), nullable=True),
        sa.Column("reason", sa.String(32), nullable=True),
        sa.Column("decision", sa.String(16), nullable=True),
        sa.Column("notice", sa.String(16), nullable=True),
        sa.Column("level", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_vision_observations_job", "vision_observations", ["job_id", "captured_at"])
    op.create_index("ix_vision_observations_device_id", "vision_observations", ["device_id"])
    op.create_index("ix_vision_observations_task_id", "vision_observations", ["task_id"])
    op.create_table(
        "vision_daily",
        sa.Column("job_id", sa.String(64), primary_key=True),
        sa.Column("day", sa.String(10), primary_key=True),
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("notices", sa.Integer(), nullable=False),
        sa.Column("first_alert_at", sa.DateTime(), nullable=True),
        sa.Column("last_alert_at", sa.DateTime(), nullable=True),
        sa.Column("longest_streak", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_vision_daily_device_id", "vision_daily", ["device_id"])


def downgrade():
    op.drop_index("ix_vision_daily_device_id", "vision_daily")
    op.drop_table("vision_daily")
    op.drop_index("ix_vision_observations_task_id", "vision_observations")
    op.drop_index("ix_vision_observations_device_id", "vision_observations")
    op.drop_index("ix_vision_observations_job", "vision_observations")
    op.drop_table("vision_observations")
    with op.batch_alter_table("scheduled_jobs") as batch:
        batch.drop_column("look_state")
        batch.drop_column("look")
