# app/auth.py
import uuid
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from .db import get_db

def get_current_user_id(db: Session = Depends(get_db)) -> uuid.UUID:
    row = db.execute(text("SELECT id FROM users ORDER BY created_at LIMIT 1")).first()
    if row:
        return row[0]
    new_id = uuid.uuid4()
    # only id is required; created_at defaults to now()
    db.execute(text("INSERT INTO users (id) VALUES (:id)"), {"id": str(new_id)})
    db.commit()
    return new_id
