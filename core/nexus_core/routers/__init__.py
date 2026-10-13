from .auth import router as auth_router
from .chats import router as chats_router
from .documents import router as documents_router
from .graph import router as graph_router
from .maintenance import router as maintenance_router
from .projects import router as projects_router
from .voice_rtc import router as voice_rtc_router

__all__ = [
    "auth_router",
    "chats_router",
    "documents_router",
    "graph_router",
    "maintenance_router",
    "projects_router",
    "voice_rtc_router",
]
