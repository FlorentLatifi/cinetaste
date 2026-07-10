from fastapi import APIRouter

from app.api.routes import auth, catalog, health, me, onboarding, titles

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(me.router, tags=["me"])
api_router.include_router(onboarding.router, tags=["onboarding"])
api_router.include_router(titles.router, tags=["titles"])
api_router.include_router(catalog.router, tags=["catalog"])
