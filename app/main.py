# app/main.py
from fastapi import FastAPI
from .routers.tracks import router as tracks_router

app = FastAPI(title="Bikepacking Tracker API")
app.include_router(tracks_router)
