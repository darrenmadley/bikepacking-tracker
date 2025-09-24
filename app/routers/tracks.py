# app/routers/tracks.py
import uuid
import logging
from datetime import timezone, datetime, timedelta
from typing import Optional, Dict, List, Tuple

import gpxpy
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..db import get_db
from ..auth import get_current_user_id

router = APIRouter(prefix="/tracks", tags=["tracks"])
log = logging.getLogger(__name__)


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    from math import radians, sin, cos, asin, sqrt
    R = 6371000.0
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = phi2 - phi1
    dl = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dl / 2) ** 2
    return 2 * R * asin(sqrt(a))


def to_utc(dt):
    if dt is None:
        return None
    # Treat naive timestamps as UTC
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def get_columns(db: Session, table: str) -> List[str]:
    rows = db.execute(
        text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
        """),
        {"t": table},
    ).fetchall()
    return [r[0] for r in rows]


def get_column_types(db: Session, table: str) -> Dict[str, Tuple[str, str]]:
    """
    Returns {column_name: (data_type, udt_name)}, e.g.
      {"id": ("bigint", "int8")} or {"id": ("uuid","uuid")}
    """
    rows = db.execute(
        text("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
        """),
        {"t": table},
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def get_geom_info(db: Session) -> Tuple[int, int]:
    """
    Returns (coord_dimension, srid) for public.track_points.geom.
    Defaults to (2, 4326) if not found.
    """
    try:
        r = db.execute(
            text("""
                SELECT coord_dimension, srid
                FROM geometry_columns
                WHERE f_table_schema='public'
                  AND f_table_name='track_points'
                  AND f_geometry_column='geom'
                LIMIT 1
            """)
        ).first()
        if r:
            dim = int(r[0] or 2)
            srid = int(r[1] or 4326)
            return dim, srid
    except Exception:
        pass
    return 2, 4326


def build_insert_sql(table: str, data: Dict[str, object]) -> (str, Dict[str, object]):
    cols = list(data.keys())
    placeholders = [f":{c}" for c in cols]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(placeholders)})"
    return sql, data


@router.post("/upload")
def upload_gpx(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user_id=Depends(get_current_user_id),
):
    # ensure multipart is available
    try:
        import multipart  # noqa: F401
    except Exception:
        raise HTTPException(status_code=500, detail="python-multipart not installed. Run: pip install python-multipart")

    if not file.filename.lower().endswith(".gpx"):
        raise HTTPException(status_code=400, detail="Only .gpx files are supported right now.")

    raw = file.file.read()
    try:
        gpx = gpxpy.parse(raw.decode("utf-8", errors="ignore"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid GPX: {e}")

    # Flatten points
    pts = []
    for trk in gpx.tracks:
        for seg in trk.segments:
            pts.extend(seg.points)

    if not pts:
        raise HTTPException(status_code=400, detail="GPX contains no points.")

    # Distance (only across points with coords)
    total_dist_m = 0.0
    for a, b in zip(pts[:-1], pts[1:]):
        if (a.latitude is not None and a.longitude is not None
                and b.latitude is not None and b.longitude is not None):
            total_dist_m += haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)

    # Time window + base timestamp
    times = [p.time for p in pts if p.time]
    if times:
        started_at: Optional[datetime] = to_utc(min(times))
        ended_at:   Optional[datetime] = to_utc(max(times))
        base_ts = started_at
    else:
        # No times in GPX → synthesize a reasonable window and point timestamps
        base_ts = datetime.now(timezone.utc)
        started_at = base_ts
        ended_at   = base_ts + timedelta(seconds=max(len(pts) - 1, 0))

    # Only insert columns that exist
    tracks_cols = set(get_columns(db, "tracks"))
    tpoints_cols = set(get_columns(db, "track_points"))
    tpoints_types = get_column_types(db, "track_points")

    track_id = uuid.uuid4()
    proposed = {
        "id": str(track_id),
        "user_id": str(user_id),
        "name": file.filename.rsplit(".", 1)[0],
        "started_at": started_at,
        "ended_at": ended_at,
        "total_distance_m": int(total_dist_m),
        "num_points": len(pts),
    }
    track_row = {k: v for k, v in proposed.items() if k in tracks_cols}
    if "id" not in track_row and "id" in tracks_cols:
        track_row["id"] = str(track_id)

    # Insert track
    if track_row:
        try:
            sql, params = build_insert_sql("tracks", track_row)
            db.execute(text(sql), params)
        except Exception as e:
            db.rollback()
            log.exception("Insert into tracks failed")
            raise HTTPException(status_code=500, detail=f"Insert tracks failed: {e}")

    # --- Insert into track_points ---
    inserted_points = 0

    # Branch A: PostGIS schema (geom + ts [+ elev_m] [+ id])
    if {"track_id", "geom", "ts"}.issubset(tpoints_cols):
        has_elev = "elev_m" in tpoints_cols

        # Include "id" only if it's a uuid column; skip if bigint/serial/identity
        include_id = False
        if "id" in tpoints_cols:
            dt, udt = tpoints_types.get("id", ("", ""))
            include_id = (dt == "uuid") or (udt == "uuid")

        # Geometry dimension & SRID
        geom_dim, srid = get_geom_info(db)
        makepoint_sql = (
            f"ST_SetSRID(ST_MakePoint(:lon, :lat, COALESCE(:z, 0.0)), {srid})"
            if geom_dim >= 3
            else f"ST_SetSRID(ST_MakePoint(:lon, :lat), {srid})"
        )

        cols = ["track_id", "ts", "geom"]
        if has_elev:
            cols.insert(2, "elev_m")
        if include_id:
            cols.insert(0, "id")
        named = ", ".join(cols)

        if include_id and has_elev:
            values = f"(:id, :track_id, :ts, :elev_m, {makepoint_sql})"
        elif include_id and not has_elev:
            values = f"(:id, :track_id, :ts, {makepoint_sql})"
        elif not include_id and has_elev:
            values = f"(:track_id, :ts, :elev_m, {makepoint_sql})"
        else:
            values = f"(:track_id, :ts, {makepoint_sql})"

        sql = f"INSERT INTO track_points ({named}) VALUES {values}"

        rows = []
        for i, p in enumerate(pts):
            lat = float(p.latitude) if p.latitude is not None else None
            lon = float(p.longitude) if p.longitude is not None else None
            if lat is None or lon is None:
                continue
            z = float(p.elevation) if p.elevation is not None else None
            ts_value = to_utc(p.time) if p.time else (base_ts + timedelta(seconds=i))
            row = {
                "track_id": str(track_id),
                "ts": ts_value,
                "lat": lat,
                "lon": lon,
                "z": z,  # used only if geom_dim >= 3
            }
            if has_elev:
                row["elev_m"] = z
            if include_id:
                row["id"] = str(uuid.uuid4())
            rows.append(row)

        if not rows:
            db.rollback()
            raise HTTPException(status_code=400, detail="GPX contains no valid coordinate points.")

        try:
            db.execute(text(sql), rows)  # executemany
            inserted_points = len(rows)
        except Exception as e:
            db.rollback()
            log.exception("Insert into track_points (PostGIS) failed")
            raise HTTPException(status_code=500, detail=f"Insert track_points (geom) failed: {e}")

    else:
        # Branch B: classic schema(s)
        classic_templates = [
            ["track_id", "seq", "lat", "lon", "ele", "t"],
            ["track_id", "seq", "lat", "lon", "t"],
            ["track_id", "seq", "lat", "lng", "ele", "t"],
        ]
        template = next((tpl for tpl in classic_templates if set(tpl).issubset(tpoints_cols)), None)
        if not template:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"track_points schema unsupported. Columns found: {sorted(tpoints_cols)}",
            )

        rows: List[Dict[str, object]] = []
        for i, p in enumerate(pts):
            row = {"track_id": str(track_id), "seq": i}
            if "lat" in template:
                row["lat"] = float(p.latitude) if p.latitude is not None else None
            if "lon" in template:
                row["lon"] = float(p.longitude) if p.longitude is not None else None
            if "lng" in template:
                row["lng"] = float(p.longitude) if p.longitude is not None else None
            if "ele" in template:
                row["ele"] = float(p.elevation) if p.elevation is not None else None
            if "t" in template:
                row["t"] = to_utc(p.time) if p.time else (base_ts + timedelta(seconds=i))
            rows.append(row)

        placeholders = ", ".join(template)
        values = ", ".join([f":{c}" for c in template])
        sql_points = f"INSERT INTO track_points ({placeholders}) VALUES ({values})"

        try:
            db.execute(text(sql_points), rows)
            inserted_points = len(rows)
        except Exception as e:
            db.rollback()
            log.exception("Insert into track_points (classic) failed")
            raise HTTPException(status_code=500, detail=f"Insert track_points failed: {e}")

    db.commit()

    return {
        "id": str(track_id),
        "points": inserted_points or 0,
        "distance_m": int(total_dist_m),
        "started_at": started_at,
        "ended_at": ended_at,
    }


@router.get("")
def list_tracks(
    limit: int = 25,
    offset: int = 0,
    db: Session = Depends(get_db),
    user_id=Depends(get_current_user_id),
):
    # Safe dynamic ORDER BY depending on presence of created_at
    track_cols = set(get_columns(db, "tracks"))
    order_sql = "ORDER BY started_at NULLS LAST" + (", created_at DESC" if "created_at" in track_cols else "")

    sql = text(f"""
        SELECT * FROM tracks
        WHERE user_id = CAST(:uid AS uuid)
        {order_sql}
        LIMIT :limit OFFSET :offset
    """)

    rows = db.execute(sql, {"uid": str(user_id), "limit": limit, "offset": offset}).mappings().all()
    return {"count": len(rows), "items": [dict(r) for r in rows]}


@router.get("/{track_id}")
def get_track(track_id: uuid.UUID, db: Session = Depends(get_db), user_id=Depends(get_current_user_id)):
    row = db.execute(text("SELECT * FROM tracks WHERE id = :id"), {"id": str(track_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Track not found")

    # Bounds from PostGIS geom if available; else from lat/lon
    tpoints_cols = set(get_columns(db, "track_points"))

    bounds = None
    if "geom" in tpoints_cols:
        b = db.execute(
            text("""
                SELECT
                  ST_YMin(ST_Extent(geom)) AS min_lat,
                  ST_YMax(ST_Extent(geom)) AS max_lat,
                  ST_XMin(ST_Extent(geom)) AS min_lon,
                  ST_XMax(ST_Extent(geom)) AS max_lon
                FROM track_points
                WHERE track_id = :id
            """),
            {"id": str(track_id)},
        ).mappings().first()
        if b and any(v is not None for v in b.values()):
            bounds = dict(b)
    else:
        b = db.execute(
            text("""
                SELECT
                  MIN(lat) AS min_lat, MAX(lat) AS max_lat,
                  MIN(COALESCE(lon,lng)) AS min_lon, MAX(COALESCE(lon,lng)) AS max_lon
                FROM track_points
                WHERE track_id = :id
            """),
            {"id": str(track_id)},
        ).mappings().first()
        if b and any(v is not None for v in b.values()):
            bounds = dict(b)

    return {"track": {k: row[k] for k in row.keys()}, "bounds": bounds}


@router.get("/{track_id}/points")
def get_track_points(
    track_id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id=Depends(get_current_user_id),
    limit: int = 0,          # 0 = all
    downsample: int = 0,     # keep every Nth point
):
    # Ensure the track exists
    exists = db.execute(text("SELECT 1 FROM tracks WHERE id = :id"), {"id": str(track_id)}).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Track not found")

    cols = set(get_columns(db, "track_points"))
    if {"geom", "ts"}.issubset(cols):
        base_sql = """
            SELECT ts,
                   elev_m,
                   ST_Y(geom) AS lat,
                   ST_X(geom) AS lon,
                   CASE WHEN ST_Z(geom) IS NULL THEN NULL ELSE ST_Z(geom) END AS z
            FROM track_points
            WHERE track_id = :id
            ORDER BY ts ASC
        """
    elif {"lat", "lon", "t"}.issubset(cols):
        base_sql = """
            SELECT t AS ts, ele AS elev_m, lat, lon, NULL AS z
            FROM track_points
            WHERE track_id = :id
            ORDER BY t ASC
        """
    elif {"lat", "lng", "t"}.issubset(cols):
        base_sql = """
            SELECT t AS ts, ele AS elev_m, lat, lng AS lon, NULL AS z
            FROM track_points
            WHERE track_id = :id
            ORDER BY t ASC
        """
    else:
        raise HTTPException(status_code=500, detail=f"Unsupported track_points schema: {sorted(cols)}")

    params = {"id": str(track_id)}
    sql = base_sql
    if downsample and downsample > 1:
        sql = f"""
            WITH ordered AS (
              SELECT *, ROW_NUMBER() OVER (ORDER BY ts ASC) rn
              FROM ({base_sql}) q
            )
            SELECT ts, elev_m, lat, lon, z
            FROM ordered
            WHERE rn % :ds = 0
        """
        params["ds"] = downsample
    if limit and limit > 0:
        sql += " LIMIT :limit"
        params["limit"] = limit

    rows = db.execute(text(sql), params).mappings().all()
    return {"count": len(rows), "points": [dict(r) for r in rows]}


@router.get("/{track_id}/line")
def get_track_line(
    track_id: uuid.UUID,
    db: Session = Depends(get_db),
    user_id=Depends(get_current_user_id),
    simplify: float = 0.0,   # tolerance in meters for Visvalingam-Whyatt
):
    cols = set(get_columns(db, "track_points"))
    if "geom" not in cols or "ts" not in cols:
        raise HTTPException(status_code=500, detail=f"Unsupported track_points schema: {sorted(cols)}")

    if simplify and simplify > 0:
        geom_expr = "ST_SimplifyVW(ST_MakeLine(ST_Force2D(geom) ORDER BY ts), :tol)"
        params = {"id": str(track_id), "tol": simplify}
    else:
        geom_expr = "ST_MakeLine(ST_Force2D(geom) ORDER BY ts)"
        params = {"id": str(track_id)}

    row = db.execute(
        text(f"SELECT ST_AsGeoJSON({geom_expr}) AS geojson FROM track_points WHERE track_id = :id"),
        params
    ).first()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="No geometry for this track")

    return {"type": "Feature", "geometry": row[0], "properties": {"track_id": str(track_id)}}
