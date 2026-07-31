from fastapi import APIRouter

from api.v1.client import auth, commitments, user

api_router = APIRouter(prefix="/v1")
api_router.include_router(auth.router)
api_router.include_router(user.router)
api_router.include_router(commitments.router)
