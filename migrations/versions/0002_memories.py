"""Persist environmental state, task memory, and session-scoped dialogue memory."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "environment_states",
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), primary_key=True),
        sa.Column("rule_id", sa.String(64), primary_key=True),
        sa.Column("state", sa.JSON(), nullable=False),
    )
    op.create_table(
        "task_memory",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("event_status", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("response", sa.JSON()),
    )
    op.create_index("ix_task_memory_device_id", "task_memory", ["device_id"])
    op.create_index("ix_task_memory_status", "task_memory", ["status"])
    op.create_table(
        "conversation_memory",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("user_text", sa.Text(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_conversation_scope", "conversation_memory", ["device_id", "session_id", "id"]
    )
    op.create_index(
        "uq_conversation_request",
        "conversation_memory",
        ["device_id", "session_id", "request_id"],
        unique=True,
    )


def downgrade():
    op.drop_table("conversation_memory")
    op.drop_table("task_memory")
    op.drop_table("environment_states")
