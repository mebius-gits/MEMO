"""Schedules in seconds: 「10 秒後提醒我」, a look task every 30 seconds."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("scheduled_jobs") as batch:
        batch.add_column(sa.Column("seconds", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("scheduled_jobs") as batch:
        batch.drop_column("seconds")
