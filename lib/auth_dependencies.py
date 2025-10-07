"""
FastAPI Authentication Dependencies
Epic 3 - API/Auth & WebSocket

Provides FastAPI dependency injection for authentication and authorization.
"""

from typing import Optional, Annotated
from fastapi import Depends, HTTPException, status, Header
from fastapi.security import OAuth2PasswordBearer, HTTPBearer, HTTPAuthorizationCredentials

from lib.auth import (
    
    User,
    UserRole,
    Permission,
    Token,
    TokenData,
    authenticate_user,
    authenticate_api_key,
    create_access_token,
    create_refresh_token,
    decode_token,
    has_permission,
    get_user,
    get_user_permissions,
)


# OAuth2 scheme for password flow
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/auth/token",
    auto_error=False  # Allow optional authentication
)

# HTTP Bearer scheme for API keys
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
    api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> Optional[User]:
    """
    Get current user from JWT token or API key (optional).
    Returns None if no valid credentials provided.
    """
    # Try JWT token first
    if token:
        token_data = decode_token(token)
        if token_data:
            user = get_user(token_data.username)
            if user and not user.disabled:
                return user

    # Try API key
    if api_key:
        api_key_obj = authenticate_api_key(api_key)
        if api_key_obj:
            # Create a pseudo-user from API key
            return User(
                username=api_key_obj.key_id,
                email=f"{api_key_obj.name}@api.local",
                full_name=api_key_obj.name,
                role=api_key_obj.role
            )

    return None


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    api_key: Optional[str] = Header(None, alias="X-API-Key")
) -> User:
    """
    Get current user from JWT token or API key (required).
    Raises 401 if no valid credentials provided.
    """
    user = await get_current_user_optional(token, api_key)

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Get current active (non-disabled) user."""
    if current_user.disabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    return current_user


def require_role(required_role: UserRole):
    """
    Dependency to require a specific role.

    Usage:
        @app.get("/admin")
        async def admin_only(user: User = Depends(require_role(UserRole.ADMIN))):
            ...
    """
    async def role_checker(current_user: User = Depends(get_current_active_user)) -> User:
        if current_user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {required_role.value} role"
            )
        return current_user
    return role_checker


def require_any_role(*roles: UserRole):
    """
    Dependency to require any of the specified roles.

    Usage:
        @app.get("/trading")
        async def trading_endpoint(
            user: User = Depends(require_any_role(UserRole.ADMIN, UserRole.TRADER))
        ):
            ...
    """
    async def role_checker(current_user: User = Depends(get_current_active_user)) -> User:
        if current_user.role not in roles:
            role_names = ", ".join(r.value for r in roles)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {role_names}"
            )
        return current_user
    return role_checker


def require_permission(required_permission: Permission):
    """
    Dependency to require a specific permission.

    Usage:
        @app.post("/signals")
        async def create_signal(
            user: User = Depends(require_permission(Permission.WRITE_SIGNALS))
        ):
            ...
    """
    async def permission_checker(current_user: User = Depends(get_current_active_user)) -> User:
        if not has_permission(current_user.role, required_permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires {required_permission.value} permission"
            )
        return current_user
    return permission_checker


def require_any_permission(*permissions: Permission):
    """
    Dependency to require any of the specified permissions.

    Usage:
        @app.get("/data")
        async def get_data(
            user: User = Depends(require_any_permission(
                Permission.READ_SIGNALS,
                Permission.READ_ORDERS
            ))
        ):
            ...
    """
    async def permission_checker(current_user: User = Depends(get_current_active_user)) -> User:
        user_permissions = get_user_permissions(current_user.role)
        if not any(p in user_permissions for p in permissions):
            perm_names = ", ".join(p.value for p in permissions)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of: {perm_names}"
            )
        return current_user
    return permission_checker


class RateLimiter:
    """Simple in-memory rate limiter for API endpoints."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = {}

    async def __call__(self, current_user: User = Depends(get_current_user)) -> User:
        """Check rate limit for current user."""
        import time

        now = time.time()
        user_key = current_user.username

        # Initialize or clean old requests
        if user_key not in self.requests:
            self.requests[user_key] = []

        # Remove requests outside window
        self.requests[user_key] = [
            req_time for req_time in self.requests[user_key]
            if now - req_time < self.window_seconds
        ]

        # Check limit
        if len(self.requests[user_key]) >= self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {self.max_requests} requests per {self.window_seconds}s"
            )

        # Record request
        self.requests[user_key].append(now)

        return current_user


# Common rate limiters
rate_limit_strict = RateLimiter(max_requests=10, window_seconds=60)  # 10 req/min
rate_limit_normal = RateLimiter(max_requests=100, window_seconds=60)  # 100 req/min
rate_limit_relaxed = RateLimiter(max_requests=1000, window_seconds=60)  # 1000 req/min


# Type aliases for cleaner code
CurrentUser = Annotated[User, Depends(get_current_active_user)]
OptionalUser = Annotated[Optional[User], Depends(get_current_user_optional)]
AdminUser = Annotated[User, Depends(require_role(UserRole.ADMIN))]
TraderUser = Annotated[User, Depends(require_any_role(UserRole.ADMIN, UserRole.TRADER))]
