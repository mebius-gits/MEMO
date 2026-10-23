"""Light sensor readings (lux)."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("readings", sa.Column("illuminance_lux", sa.Float()))


def downgrade():
    with op.batch_alter_table("readings") as batch:
        batch.drop_column("illuminance_lux")
