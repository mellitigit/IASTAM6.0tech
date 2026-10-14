from fastapi import APIRouter

from app.api.routes import diagnostics, downlink, health, mission, queue

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(diagnostics.router)
api_router.include_router(downlink.router)
api_router.include_router(mission.router)
api_router.include_router(queue.router)
