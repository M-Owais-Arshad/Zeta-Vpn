"""REST API routers for the ZetaVPN panel."""

from fastapi import APIRouter

from . import auth, clients, inbounds, ssh, subscription, system
from . import settings as settings_api

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(inbounds.router, prefix="/inbounds", tags=["inbounds"])
api_router.include_router(clients.router, prefix="/inbounds", tags=["clients"])
api_router.include_router(ssh.router, prefix="/ssh", tags=["ssh"])
api_router.include_router(settings_api.router, prefix="/settings", tags=["settings"])

# Subscription is mounted at the app root (not under /api) so links stay short.
sub_router = subscription.router

__all__ = ["api_router", "sub_router"]
