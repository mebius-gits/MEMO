"""Create devices and readings."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "devices",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("location", sa.String(200), nullable=False),
    )
    op.create_table(
        "readings",
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("temperature_c", sa.Float()),
        sa.Column("humidity_pct", sa.Float()),
        sa.Column("co2_ppm", sa.Float()),
        sa.Column("quality", sa.String(16), nullable=False),
    )
    op.create_index("ix_readings_device_observed", "readings", ["device_id", "observed_at"])


def downgrade():
    op.drop_index("ix_readings_device_observed", table_name="readings")
    op.drop_table("readings")
    op.drop_table("devices")
