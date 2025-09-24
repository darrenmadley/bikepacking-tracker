from __future__ import annotations
from sqlalchemy.orm import declarative_base, Mapped, mapped_column, relationship
from sqlalchemy import String, DateTime, Float, ForeignKey, Enum, JSON, Index, UniqueConstraint, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from geoalchemy2 import Geometry
from datetime import datetime
import uuid, enum

Base = declarative_base()

class DeviceType(enum.Enum):
    spot = "spot"
    inreach = "inreach"
    other = "other"

class RouteSource(enum.Enum):
    gpx = "gpx"
    rwgps = "rwgps"
    strava = "strava"
    manual = "manual"

class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    home_geom = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class Device(Base):
    __tablename__ = "devices"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[DeviceType] = mapped_column(Enum(DeviceType, name="device_type"))
    external_id: Mapped[str] = mapped_column(String(128), index=True)
    secret: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    user = relationship("User", backref="devices")
    __table_args__ = (UniqueConstraint("type","external_id", name="uq_device_provider_extid"),)

class Route(Base):
    __tablename__ = "routes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[RouteSource] = mapped_column(Enum(RouteSource, name="route_source"))
    distance_m: Mapped[float | None] = mapped_column(Float)
    ascent_m: Mapped[float | None] = mapped_column(Float)
    descent_m: Mapped[float | None] = mapped_column(Float)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geom = mapped_column(Geometry(geometry_type="LINESTRINGZ", srid=4326))
    bbox = mapped_column(Geometry(geometry_type="POLYGON", srid=4326), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    user = relationship("User", backref="routes")
    __table_args__ = (Index("ix_routes_geom", "geom", postgresql_using="gist"),)

class Track(Base):
    __tablename__= "tracks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    route_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("routes.id", ondelete="SET NULL"))
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"))
    name: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_distance_m: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

class TrackPoint(Base):
    __tablename__ = "track_points"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    track_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    elev_m: Mapped[float | None] = mapped_column(Float)
    geom = mapped_column(Geometry(geometry_type="POINTZ", srid=4326))
    __table_args__ = (
        Index("ix_track_points_geom", "geom", postgresql_using="gist"),
        Index("ix_track_points_track_ts","track_id","ts"),
    )

class LivePosition(Base):
    __tablename__ = "live_positions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    geom = mapped_column(Geometry(geometry_type="POINTZ", srid=4326))
    battery: Mapped[float | None] = mapped_column(Float)
    extra = mapped_column(JSON, nullable=True)
    __table_args__ = (
        Index("ix_live_positions_geom","geom", postgresql_using="gist"),
        UniqueConstraint("device_id","ts", name="uq_live_device_ts"),
    )
