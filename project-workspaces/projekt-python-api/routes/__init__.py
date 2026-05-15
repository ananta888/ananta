from routes.auth import router as auth_router
from routes.users import router as users_router
from routes.posts import router as posts_router
from routes.comments import router as comments_router

__all__ = ["auth_router", "users_router", "posts_router", "comments_router"]
