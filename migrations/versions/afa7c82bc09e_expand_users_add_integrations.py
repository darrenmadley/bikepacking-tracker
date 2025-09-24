"""expand users + add integrations

Revision ID: afa7c82bc09e
Revises: dd410ac30f13
Create Date: 2025-09-23 10:11:29.172619
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "afa7c82bc09e"
down_revision: Union[str, Sequence[str], None] = "dd410ac30f13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Extensions (safe if run repeatedly)
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # Enums (idempotent)
    units = sa.Enum("metric", "imperial", name="units")
    units.create(op.get_bind(), checkfirst=True)
    role = sa.Enum("user", "admin", name="user_role")
    role.create(op.get_bind(), checkfirst=True)
    auth_provider = sa.Enum("auth0", "firebase", "local", "none", name="auth_provider")
    auth_provider.create(op.get_bind(), checkfirst=True)
    integ_provider = sa.Enum("rwgps", "strava", "garmin", "inreach", "spot", name="integration_provider")
    integ_provider.create(op.get_bind(), checkfirst=True)
    integ_status = sa.Enum("linked", "revoked", "error", "pending", name="integration_status")
    integ_status.create(op.get_bind(), checkfirst=True)

    # === users: add new columns; DO NOT touch existing email/home_geom/name ===
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("display_name", sa.String(120), nullable=True))
        batch.add_column(sa.Column("photo_url", sa.String(2048), nullable=True))
        batch.add_column(sa.Column("avatar_url", sa.String(2048), nullable=True))
        batch.add_column(sa.Column("timezone", sa.String(64), server_default="UTC", nullable=True))
        batch.add_column(sa.Column("units", sa.Enum("metric", "imperial", name="units"),
                                   server_default="metric", nullable=False))
        batch.add_column(sa.Column("role", sa.Enum("user", "admin", name="user_role"),
                                   server_default="user", nullable=False))
        batch.add_column(sa.Column("auth_provider", sa.Enum("auth0", "firebase", "local", "none", name="auth_provider"),
                                   server_default="none", nullable=False))
        batch.add_column(sa.Column("auth_sub", sa.String(255), nullable=True))
        batch.add_column(sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False))
        batch.add_column(sa.Column("email_verified", sa.Boolean(), server_default=sa.text("false"), nullable=False))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
        batch.add_column(sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()),
                                   server_default=sa.text("'{}'::jsonb"), nullable=False))

    # Unique only when both present
    op.create_index(
        "uq_users_auth_provider_sub",
        "users",
        ["auth_provider", "auth_sub"],
        unique=True,
        postgresql_where=sa.text("auth_sub IS NOT NULL AND auth_provider <> 'none'"),
    )

    # === user_integrations ===
    op.create_table(
        "user_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.Enum("rwgps", "strava", "garmin", "inreach", "spot", name="integration_provider"), nullable=False),
        sa.Column("status", sa.Enum("linked", "revoked", "error", "pending", name="integration_status"),
                  server_default="pending", nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("external_user_id", sa.String(255), nullable=True),
        sa.Column("settings", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_provider"),
    )

    # === user_integration_tokens ===
    op.create_table(
        "user_integration_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("user_integrations.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("access_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # === user_notes ===
    op.create_table(
        "user_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("user_notes")
    op.drop_table("user_integration_tokens")
    op.drop_table("user_integrations")
    op.drop_index("uq_users_auth_provider_sub", table_name="users")

    with op.batch_alter_table("users") as batch:
        for col in [
            "settings", "last_login_at", "updated_at", "email_verified", "is_active",
            "auth_sub", "auth_provider", "role", "units", "timezone",
            "avatar_url", "photo_url", "display_name",
        ]:
            try:
                batch.drop_column(col)
            except Exception:
                pass

    # Drop enums (safe if they exist)
    for enum_name in ["integration_status", "integration_provider", "auth_provider", "user_role", "units"]:
        try:
            sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
        except Exception:
            pass
