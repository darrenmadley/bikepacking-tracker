# app/routers/spot.py
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from xml.etree import ElementTree as ET

from ..db import get_db
from ..auth import get_current_user_id

router = APIRouter(prefix="/spot", tags=["spot"])
log = logging.getLogger(__name__)


# ---------- DB helpers (introspect & geometry info) ----------

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

def get_enum_labels(db: Session, type_name: str) -> List[str]:
    """Return labels for a Postgres enum type."""
    rows = db.execute(
        text("""
            SELECT e.enumlabel
            FROM pg_type t
            JOIN pg_enum e ON e.enumtypid = t.oid
            WHERE t.typname = :t
            ORDER BY e.enumsortorder
        """),
        {"t": type_name},
    ).fetchall()
    return [r[0] for r in rows]


def get_column_types(db: Session, table: str) -> Dict[str, Tuple[str, str]]:
    rows = db.execute(
        text("""
            SELECT column_name, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
        """),
        {"t": table},
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def get_geom_info(db: Session, table: str, geom_col: str = "geom") -> Tuple[int, int]:
    """
    Returns (coord_dimension, srid) for public.<table>.<geom_col>.
    Defaults to (2, 4326) if not found.
    """
    try:
        r = db.execute(
            text("""
                SELECT coord_dimension, srid
                FROM geometry_columns
                WHERE f_table_schema='public'
                  AND f_table_name=:t
                  AND f_geometry_column=:c
                LIMIT 1
            """),
            {"t": table, "c": geom_col},
        ).first()
        if r:
            dim = int(r[0] or 2)
            srid = int(r[1] or 4326)
            return dim, srid
    except Exception:
        pass
    return 2, 4326


# ---------- SPOT feed fetching & parsing ----------

def _parse_dt(unix: Optional[str], date_str: Optional[str]) -> datetime:
    # Prefer unixTime if present; else try dateTime like "2024-07-01T12:34:56+0000"
    if unix:
        try:
            return datetime.fromtimestamp(int(unix), tz=timezone.utc)
        except Exception:
            pass
    if date_str:
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
            try:
                return datetime.strptime(date_str, fmt)
            except Exception:
                continue
        # last resort: try to normalize "+00:00" vs "+0000"
        try:
            if date_str.endswith("+00:00"):
                return datetime.fromisoformat(date_str)
        except Exception:
            pass
    # fallback to now
    return datetime.now(timezone.utc)


def _safe_float(x) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def parse_spot_json(payload: dict) -> List[dict]:
    """
    Normalizes SPOT v2 JSON messages to a list of dicts:
      {lat, lon, z, ts, speed_kph, battery, msg_type, esn, message_id}
    """
    # payload looks like { "response": { "feedMessageResponse": { "messages": { "message": [ ... ]}}}}
    res = payload.get("response") or payload
    fmr = res.get("feedMessageResponse") or res.get("messages") or res
    msgs = fmr.get("messages", {})
    items = msgs.get("message") if isinstance(msgs, dict) else msgs
    if items is None:
        items = fmr.get("message")
    if items is None:
        return []

    if isinstance(items, dict):
        items = [items]

    out = []
    for m in items:
        lat = _safe_float(m.get("latitude") or m.get("lat"))
        lon = _safe_float(m.get("longitude") or m.get("lng") or m.get("lon"))
        z = _safe_float(m.get("altitude") or m.get("alt"))
        speed = _safe_float(m.get("speed"))
        # 'batteryState' can be "GOOD", etc.
        battery = (m.get("batteryState") or m.get("battery") or "").strip() or None
        msg_type = (m.get("messageType") or m.get("type") or "").strip() or None
        esn = (m.get("esn") or m.get("messengerId") or "").strip() or None
        mid = str(m.get("id") or m.get("messageId") or "").strip() or None
        ts = _parse_dt(m.get("unixTime"), m.get("dateTime") or m.get("time"))

        if lat is None or lon is None:
            continue

        out.append({
            "lat": lat, "lon": lon, "z": z, "ts": ts,
            "speed_kph": speed, "battery": battery,
            "msg_type": msg_type, "esn": esn, "message_id": mid,
        })
    return out


def parse_spot_xml(xml_bytes: bytes) -> List[dict]:
    """
    Parses SPOT XML like:
      <response><feedMessageResponse><messages><message>...</message></messages></feedMessageResponse></response>
    """
    root = ET.fromstring(xml_bytes)
    # find all <message> nodes regardless of nesting
    msgs = root.findall(".//message")
    out = []
    for m in msgs:
        def g(tag):  # first child text by tag
            el = m.find(tag)
            return el.text.strip() if el is not None and el.text is not None else None

        lat = _safe_float(g("latitude") or g("lat"))
        lon = _safe_float(g("longitude") or g("lng") or g("lon"))
        z = _safe_float(g("altitude") or g("alt"))
        speed = _safe_float(g("speed"))
        battery = (g("batteryState") or g("battery") or "").strip() or None
        msg_type = (g("messageType") or g("type") or "").strip() or None
        esn = (g("esn") or g("messengerId") or "").strip() or None
        mid = (g("id") or g("messageId") or "").strip() or None
        ts = _parse_dt(g("unixTime"), g("dateTime") or g("time"))

        if lat is None or lon is None:
            continue
        out.append({
            "lat": lat, "lon": lon, "z": z, "ts": ts,
            "speed_kph": speed, "battery": battery,
            "msg_type": msg_type, "esn": esn, "message_id": mid,
        })
    return out


async def fetch_spot_messages(feed_id: str, feed_password: Optional[str] = None) -> List[dict]:
    """
    Tries JSON first, then XML. Returns normalized list as per parsers above.
    """
    base = f"https://api.findmespot.com/spot-main-web/consumer/rest-api/2.0/public/feed/{feed_id}/message"
    params = {}
    if feed_password:
        params["feedPassword"] = feed_password

    async with httpx.AsyncClient(timeout=20) as client:
        # Try JSON
        try:
            rj = await client.get(base + ".json", params=params)
            if rj.status_code == 200 and rj.headers.get("content-type", "").lower().find("json") >= 0:
                data = rj.json()
                items = parse_spot_json(data)
                if items:
                    return items
        except Exception as e:
            log.warning("SPOT JSON fetch failed: %s", e)

        # Fallback to XML
        rx = await client.get(base + ".xml", params=params)
        rx.raise_for_status()
        return parse_spot_xml(rx.content)


# ---------- Inserts into live_positions (schema-aware) ----------

def _select_existing_msg_ids(db: Session, provider: str, msg_ids: List[str]) -> set:
    if not msg_ids:
        return set()
    cols = set(get_columns(db, "live_positions"))
    if "provider_msg_id" not in cols or "provider" not in cols:
        return set()
    # fetch existing ids for this provider
    # NOTE: for small batches (<200), IN list is fine.
    rows = db.execute(
        text("""
            SELECT provider_msg_id
            FROM live_positions
            WHERE provider = :p AND provider_msg_id IN :ids
        """),
        {"p": provider, "ids": tuple([mid for mid in msg_ids if mid])},
    ).fetchall()
    return {r[0] for r in rows}


def insert_positions(
    db: Session,
    user_id: uuid.UUID,
    msgs: List[dict],
) -> int:
    lp_cols = set(get_columns(db, "live_positions"))
    lp_types = get_column_types(db, "live_positions")
    lp_constraints = get_column_constraints(db, "live_positions")

    has_geom = "geom" in lp_cols
    needs_ts = "ts" in lp_cols
    has_speed_kph = "speed_kph" in lp_cols
    has_speed_mps = "speed_mps" in lp_cols
    has_battery = "battery" in lp_cols
    has_user = "user_id" in lp_cols
    has_device = "device_id" in lp_cols
    has_raw = "raw" in lp_cols  # jsonb

    # Is device_id required (NOT NULL without a default)?
    device_required = (
        has_device
        and lp_constraints.get("device_id", {}).get("is_nullable") == "NO"
        and not lp_constraints.get("device_id", {}).get("column_default")
    )

    # Prepare PostGIS geometry builder, if needed
    if has_geom:
        dim, srid = get_geom_info(db, "live_positions", "geom")
        point_sql = (
            "ST_SetSRID(ST_MakePoint(:lon, :lat, COALESCE(:z, 0.0)), {srid})".format(srid=srid)
            if dim >= 3 else
            "ST_SetSRID(ST_MakePoint(:lon, :lat), {srid})".format(srid=srid)
        )

    # If device is required, choose/create a default device for this user once
    default_device_id: Optional[str] = None
    if device_required:
        first_esn = next((m.get("esn") for m in msgs if m.get("esn")), None)
        default_device_id = ensure_spot_device_for_user(db, user_id, first_esn)

    # Normalize messages -> rows (dicts for executemany)
    rows: List[Dict[str, object]] = []
    for m in msgs:
        row: Dict[str, object] = {}
        if needs_ts:
            row["ts"] = m["ts"]

        if has_user:
            row["user_id"] = str(user_id)

        if has_geom:
            row["lat"] = m["lat"]
            row["lon"] = m["lon"]
            row["z"] = m.get("z")
        else:
            # classic lat/lon schema variants
            if "lat" in lp_cols:
                row["lat"] = m["lat"]
            if "lon" in lp_cols:
                row["lon"] = m["lon"]
            if "lng" in lp_cols:
                row["lng"] = m["lon"]
            if "ele" in lp_cols:
                row["ele"] = m.get("z")

        # speed
        if has_speed_kph and m.get("speed_kph") is not None:
            row["speed_kph"] = m["speed_kph"]
        elif has_speed_mps and m.get("speed_kph") is not None:
            row["speed_mps"] = m["speed_kph"] / 3.6

        # battery: numeric vs text, depending on column type
        if has_battery:
            batt_raw = m.get("battery")
            battery_dt, battery_udt = lp_types.get("battery", ("", ""))
            battery_is_numeric = (
                battery_dt in {"double precision", "real", "numeric"} or
                battery_udt in {"float8", "float4", "numeric"}
            )
            if battery_is_numeric:
                val = None
                if batt_raw is not None:
                    try:
                        val = float(str(batt_raw).strip())
                    except Exception:
                        val = None
                row["battery"] = val
            else:
                row["battery"] = batt_raw

        # device: try to resolve per message if schema supports provider/external_id
        if has_device:
            dev_id = None
            dev_cols = set(get_columns(db, "devices"))
            if {"provider", "external_id", "id"}.issubset(dev_cols) and m.get("esn"):
                dev = db.execute(
                    text("""
                        SELECT id FROM devices
                        WHERE provider = :p AND external_id = :ext
                          AND (:uid::uuid IS NULL OR user_id = :uid)
                        LIMIT 1
                    """),
                    {"p": "spot", "ext": m["esn"], "uid": str(user_id)},
                ).first()
                if dev:
                    dev_id = str(dev[0])

            # If NOT NULL is enforced, fall back to the default for any rows that didn't resolve
            if device_required:
                dev_id = dev_id or default_device_id

            if dev_id is not None:
                row["device_id"] = dev_id

        if has_raw:
            row["raw"] = {"msg_type": m.get("msg_type"), "esn": m.get("esn")}

        rows.append(row)

    if not rows:
        return 0

    # Decide which optional columns to include in INSERT
    include_device = has_device and all(r.get("device_id") is not None for r in rows)
    if device_required and not include_device:
        # Safety net: schema requires device_id but we couldn't produce one
        raise HTTPException(
            status_code=500,
            detail="live_positions.device_id is NOT NULL but no device_id could be resolved. "
                   "Create a SPOT device for this user or relax the NOT NULL constraint."
        )

    if has_geom:
        cols = []
        if has_user: cols.append("user_id")
        if needs_ts: cols.append("ts")
        if include_device: cols.append("device_id")
        if has_battery: cols.append("battery")
        if has_speed_kph: cols.append("speed_kph")
        if has_speed_mps: cols.append("speed_mps")
        cols.append("geom")  # always last; uses ST_MakePoint()

        # Ensure every row has all keys (fill None), and required coords exist
        for r in rows:
            for c in cols:
                if c != "geom":
                    r.setdefault(c, None)
            if "lat" not in r or "lon" not in r or r["lat"] is None or r["lon"] is None:
                r["__skip__"] = True
        rows = [r for r in rows if "__skip__" not in r]
        if not rows:
            return 0

        named = ", ".join(cols)
        parts = []
        for c in cols:
            parts.append(point_sql if c == "geom" else f":{c}")
        values = "(" + ", ".join(parts) + ")"
        sql = f"INSERT INTO live_positions ({named}) VALUES {values}"

        try:
            db.execute(text(sql), rows)
        except Exception as e:
            db.rollback()
            log.exception("Insert into live_positions (geom) failed")
            raise HTTPException(status_code=500, detail=f"Insert live_positions failed: {e}")

    else:
        # classic (lat/lon) schema; try common templates
        template_opts = [
            ["user_id", "ts", "lat", "lon"],
            ["user_id", "ts", "lat", "lng"],
            ["ts", "lat", "lon"],
            ["ts", "lat", "lng"],
        ]
        template = next((tpl for tpl in template_opts if set(tpl).issubset(lp_cols)), None)
        if not template:
            raise HTTPException(status_code=500, detail=f"live_positions schema unsupported. Columns: {sorted(lp_cols)}")

        for r in rows:
            for c in template:
                r.setdefault(c, None)

        placeholders = ", ".join(template)
        binds = ", ".join([f":{c}" for c in template])
        sql = f"INSERT INTO live_positions ({placeholders}) VALUES ({binds})"

        try:
            db.execute(text(sql), rows)
        except Exception as e:
            db.rollback()
            log.exception("Insert into live_positions (classic) failed")
            raise HTTPException(status_code=500, detail=f"Insert live_positions failed: {e}")

    db.commit()
    return len(rows)


# ---------- API endpoints ----------

@router.post("/import")
async def import_spot_feed(
    feed_id: str = Query(..., min_length=8, description="SPOT public feed ID (32 chars typical)"),
    feed_password: Optional[str] = Query(None, description="Optional password if the feed is protected"),
    db: Session = Depends(get_db),
    user_id=Depends(get_current_user_id),
):
    """
    Pulls the SPOT feed and stores positions into live_positions.
    """
    try:
        msgs = await fetch_spot_messages(feed_id, feed_password)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail=f"SPOT fetch failed: {e}")  # pass-thru code
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SPOT fetch error: {e}")

    if not msgs:
        return {"imported": 0, "reason": "no messages"}

    inserted = insert_positions(db, user_id, msgs)
    newest = max(m["ts"] for m in msgs) if msgs else None
    return {
        "imported": inserted,
        "messages_seen": len(msgs),
        "newest_ts": newest,
    }
def get_column_constraints(db: Session, table: str) -> Dict[str, Dict[str, Optional[str]]]:
    rows = db.execute(
        text("""
            SELECT column_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :t
        """),
        {"t": table},
    ).fetchall()
    return {r[0]: {"is_nullable": r[1], "column_default": r[2]} for r in rows}


def ensure_spot_device_for_user(db: Session, user_id: uuid.UUID, esn: Optional[str]) -> str:
    """
    Find or create a SPOT device for this user, satisfying NOT NULL columns.
    Returns device UUID (as string).
    """
    dev_cols = set(get_columns(db, "devices"))
    dev_types = get_column_types(db, "devices")
    dev_cons = get_column_constraints(db, "devices")

    if "id" not in dev_cols:
        raise HTTPException(status_code=500, detail="devices table missing 'id'; cannot satisfy device_id NOT NULL")

    # ----- Choose values for possibly-required columns -----
    # type
    type_val = None
    if "type" in dev_cols:
        dt, udt = dev_types.get("type", ("", ""))
        if dt == "USER-DEFINED" and udt:
            labels = get_enum_labels(db, udt)
            # prefer 'spot' if present; else first label
            type_val = next((l for l in labels if l.lower() == "spot"), labels[0] if labels else None)
        else:
            type_val = "spot"

    # provider
    provider_val = None
    if "provider" in dev_cols:
        dt, udt = dev_types.get("provider", ("", ""))
        if dt == "USER-DEFINED" and udt:
            labels = get_enum_labels(db, udt)
            provider_val = next((l for l in labels if l.lower() == "spot"), labels[0] if labels else None)
        else:
            provider_val = "spot"

    # external_id (SPOT ESN if present)
    external_id_val = (esn or "unknown") if "external_id" in dev_cols else None

    # name
    name_val = None
    if "name" in dev_cols:
        name_val = f"SPOT {esn}" if esn else "SPOT device"

    # status (some schemas have NOT NULL status)
    status_val = None
    if "status" in dev_cols:
        dt, udt = dev_types.get("status", ("", ""))
        if dt == "USER-DEFINED" and udt:
            labels = get_enum_labels(db, udt)
            # prefer 'active' if exists; else first label
            status_val = next((l for l in labels if l.lower() == "active"), labels[0] if labels else None)
        else:
            status_val = "active"

    # ----- Look up existing device (best-effort) -----
    # Prefer exact match on provider/external_id if columns exist
    if {"provider", "external_id", "id"}.issubset(dev_cols) and provider_val and external_id_val:
        params = {"p": provider_val, "ext": external_id_val, "uid": str(user_id)}
        where = "provider = :p AND external_id = :ext"
        if "user_id" in dev_cols:
            where += " AND user_id = :uid"
        row = db.execute(text(f"SELECT id FROM devices WHERE {where} LIMIT 1"), params).first()
        if row:
            return str(row[0])

    # Fallback: look up by (user_id, name) if available
    where_parts, params = [], {}
    if "user_id" in dev_cols:
        where_parts.append("user_id = :uid"); params["uid"] = str(user_id)
    if "name" in dev_cols and name_val:
        where_parts.append("name = :name"); params["name"] = name_val
    if where_parts:
        row = db.execute(text(f"SELECT id FROM devices WHERE {' AND '.join(where_parts)} LIMIT 1"), params).first()
        if row:
            return str(row[0])

    # ----- Create device, providing all NOT NULL columns -----
    dev_id = str(uuid.uuid4())
    cols, vals, bind = ["id"], [":id"], {"id": dev_id}

    def add(col: str, value):
        if col in dev_cols:
            # if NOT NULL without default, ensure we supply *some* value
            nn = dev_cons.get(col, {}).get("is_nullable") == "NO"
            has_default = dev_cons.get(col, {}).get("column_default") is not None
            if value is not None or nn and not has_default:
                cols.append(col); vals.append(f":{col}"); bind[col] = value

    add("user_id", str(user_id) if "user_id" in dev_cols else None)
    add("type", type_val)
    add("provider", provider_val)
    add("external_id", external_id_val)
    add("name", name_val)
    add("status", status_val)

    sql = f"INSERT INTO devices ({', '.join(cols)}) VALUES ({', '.join(vals)})"
    db.execute(text(sql), bind)
    return dev_id
