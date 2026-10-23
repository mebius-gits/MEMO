"""Desk lamp state per device."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lamp_states",
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), primary_key=True),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("last_on", sa.String(8)),
        sa.Column("changed_by", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("lamp_states")
