"""The room's sensors at the moment of each look photo (light, CO₂, temperature...)."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("vision_observations") as batch:
        batch.add_column(sa.Column("sensors", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("vision_observations") as batch:
        batch.drop_column("sensors")
