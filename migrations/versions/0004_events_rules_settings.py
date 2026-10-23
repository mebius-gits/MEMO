"""Event update history, editable rule thresholds and per-device preferences."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "event_updates",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("level", sa.String(16)),
        sa.Column("value", sa.Float()),
        sa.Column("detail", sa.JSON()),
        sa.Column("at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_event_updates_device_event", "event_updates", ["device_id", "event_id"])
    op.create_table(
        "rule_configs",
        sa.Column("rule_id", sa.String(64), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("override", sa.JSON(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.add_column("device_settings", sa.Column("preferences", sa.JSON()))


def downgrade():
    with op.batch_alter_table("device_settings") as batch:
        batch.drop_column("preferences")
    op.drop_table("rule_configs")
    op.drop_index("ix_event_updates_device_event", "event_updates")
    op.drop_table("event_updates")
