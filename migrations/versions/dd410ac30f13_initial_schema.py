"""initial schema

Revision ID: dd410ac30f13
Revises:
Create Date: 2025-09-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
import geoalchemy2

# revision identifiers, used by Alembic.
revision = "dd410ac30f13"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nukes from failed attempts: drop lingering enums if they exist (no tables yet, safe)
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'device_type') THEN
                DROP TYPE device_type;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'route_source') THEN
                DROP TYPE route_source;
            END IF;
        END$$;
        """
    )

    # Define named enums (let table DDL create them once)
    device_type = sa.Enum("spot", "inreach", "other", name="device_type")
    route_source = sa.Enum("gpx", "rwgps", "strava", "manual", name="route_source")

    # users
    op.create_table(
        "users",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=320), unique=True, nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column(
            "home_geom",
            geoalchemy2.types.Geometry(geometry_type="POINT", srid=4326, spatial_index=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # devices
    op.create_table(
        "devices",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", device_type, nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("secret", sa.String(length=256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("type", "external_id", name="uq_device_provider_extid"),
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"], unique=False)
    op.create_index("ix_devices_external_id", "devices", ["external_id"], unique=False)

    # routes
    op.create_table(
        "routes",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source", route_source, nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("ascent_m", sa.Float(), nullable=True),
        sa.Column("descent_m", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="LINESTRINGZ", srid=4326, spatial_index=True),
            nullable=False,
        ),
        sa.Column(
            "bbox",
            geoalchemy2.types.Geometry(geometry_type="POLYGON", srid=4326, spatial_index=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_routes_user_id", "routes", ["user_id"], unique=False)

    # tracks
    op.create_table(
        "tracks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("route_id", pg.UUID(as_uuid=True), sa.ForeignKey("routes.id", ondelete="SET NULL"), nullable=True),
        sa.Column("device_id", pg.UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_distance_m", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tracks_user_id", "tracks", ["user_id"], unique=False)

    # track_points
    op.create_table(
        "track_points",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("track_id", pg.UUID(as_uuid=True), sa.ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("elev_m", sa.Float(), nullable=True),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="POINTZ", srid=4326, spatial_index=True),
            nullable=False,
        ),
    )
    op.create_index("ix_track_points_track_id", "track_points", ["track_id"], unique=False)
    op.create_index("ix_track_points_ts", "track_points", ["ts"], unique=False)
    op.create_index("ix_track_points_track_ts", "track_points", ["track_id", "ts"], unique=False)

    # live_positions
    op.create_table(
        "live_positions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("device_id", pg.UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(geometry_type="POINTZ", srid=4326, spatial_index=True),
            nullable=False,
        ),
        sa.Column("battery", sa.Float(), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.UniqueConstraint("device_id", "ts", name="uq_live_device_ts"),
    )
    op.create_index("ix_live_positions_device_id", "live_positions", ["device_id"], unique=False)
    op.create_index("ix_live_positions_ts", "live_positions", ["ts"], unique=False)


def downgrade() -> None:
    # Drop in reverse order
    op.drop_index("ix_live_positions_ts", table_name="live_positions")
    op.drop_index("ix_live_positions_device_id", table_name="live_positions")
    op.drop_table("live_positions")

    op.drop_index("ix_track_points_track_ts", table_name="track_points")
    op.drop_index("ix_track_points_ts", table_name="track_points")
    op.drop_index("ix_track_points_track_id", table_name="track_points")
    op.drop_table("track_points")

    op.drop_index("ix_tracks_user_id", table_name="tracks")
    op.drop_table("tracks")

    op.drop_index("ix_routes_user_id", table_name="routes")
    op.drop_table("routes")

    op.drop_index("ix_devices_external_id", table_name="devices")
    op.drop_index("ix_devices_user_id", table_name="devices")
    op.drop_table("devices")

    op.drop_table("users")

    # Clean up enums if they exist
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'route_source') THEN
                DROP TYPE route_source;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'device_type') THEN
                DROP TYPE device_type;
            END IF;
        END$$;
        """
    )

