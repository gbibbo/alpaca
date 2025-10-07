"""
Authentication API Routes
Epic 3 - API/Auth & WebSocket

Provides authentication endpoints for login, token refresh, and API key management.
"""

from datetime import timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr

from lib.auth import (
    User,
    UserRole,
    Token,
    APIKey,
    authenticate_user,
    create_access_token,
    create_refresh_token,
    create_user,
    create_api_key_for_client,
    revoke_api_key,
    get_user_permissions,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    API_KEYS_DB,
    USERS_DB,
)
from lib.auth_dependencies import (
    CurrentUser,
    AdminUser,
    require_permission,
)
from lib.auth import Permission


router = APIRouter(prefix="/api/auth", tags=["Authentication"])


class LoginResponse(BaseModel):
    """Login response with tokens."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: User


class UserCreate(BaseModel):
    """User creation request."""
    username: str
    email: EmailStr
    password: str
    role: UserRole
    full_name: Optional[str] = None


class APIKeyCreate(BaseModel):
    """API key creation request."""
    name: str
    role: UserRole
    expires_in_days: Optional[int] = None


class APIKeyResponse(BaseModel):
    """API key creation response."""
    key_id: str
    api_key: str
    name: str
    role: UserRole
    expires_at: Optional[str] = None
    message: str = "Save this API key now. You won't be able to see it again!"


class PermissionsResponse(BaseModel):
    """User permissions response."""
    username: str
    role: UserRole
    permissions: List[str]


@router.post("/token", response_model=LoginResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    OAuth2 compatible token login.

    Use username and password to get access and refresh tokens.
    """
    user = authenticate_user(form_data.username, form_data.password)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get user permissions
    permissions = get_user_permissions(user.role)

    # Create tokens
    access_token = create_access_token(
        data={
            "sub": user.username,
            "role": user.role.value,
            "permissions": [p.value for p in permissions]
        },
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    refresh_token = create_refresh_token(user.username)

    # Update last login
    user.last_login = None  # Would update in real DB

    return LoginResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=User(**user.model_dump(exclude={"hashed_password"}))
    )


@router.post("/refresh", response_model=Token)
async def refresh_token(refresh_token: str):
    """
    Refresh access token using refresh token.

    Provide a valid refresh token to get a new access token.
    """
    from lib.auth import decode_token

    token_data = decode_token(refresh_token)

    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

    # Get user
    user = get_user(token_data.username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    # Get permissions
    permissions = get_user_permissions(user.role)

    # Create new access token
    access_token = create_access_token(
        data={
            "sub": user.username,
            "role": user.role.value,
            "permissions": [p.value for p in permissions]
        }
    )

    return Token(
        access_token=access_token,
        expires_in=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


@router.get("/me", response_model=User)
async def get_current_user_info(current_user: CurrentUser):
    """
    Get current authenticated user information.

    Returns the profile of the currently authenticated user.
    """
    return current_user


@router.get("/me/permissions", response_model=PermissionsResponse)
async def get_current_user_permissions(current_user: CurrentUser):
    """
    Get current user's permissions.

    Returns all permissions available to the current user based on their role.
    """
    permissions = get_user_permissions(current_user.role)

    return PermissionsResponse(
        username=current_user.username,
        role=current_user.role,
        permissions=[p.value for p in permissions]
    )


@router.post("/users", response_model=User)
async def create_new_user(
    user_data: UserCreate,
    admin_user: AdminUser
):
    """
    Create a new user (Admin only).

    Only administrators can create new users.
    """
    try:
        user = create_user(
            username=user_data.username,
            email=user_data.email,
            password=user_data.password,
            role=user_data.role,
            full_name=user_data.full_name
        )

        return User(**user.model_dump(exclude={"hashed_password"}))

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.get("/users", response_model=List[User])
async def list_users(admin_user: AdminUser):
    """
    List all users (Admin only).

    Returns a list of all registered users.
    """
    users = []
    for user_db in USERS_DB.values():
        users.append(User(**user_db.model_dump(exclude={"hashed_password"})))

    return users


@router.post("/api-keys", response_model=APIKeyResponse)
async def create_new_api_key(
    key_data: APIKeyCreate,
    admin_user: AdminUser
):
    """
    Create a new API key (Admin only).

    Generate a new API key for machine-to-machine authentication.
    The API key will only be shown once - save it securely!
    """
    api_key_obj, api_key = create_api_key_for_client(
        name=key_data.name,
        role=key_data.role,
        expires_in_days=key_data.expires_in_days
    )

    return APIKeyResponse(
        key_id=api_key_obj.key_id,
        api_key=api_key,
        name=api_key_obj.name,
        role=api_key_obj.role,
        expires_at=api_key_obj.expires_at.isoformat() if api_key_obj.expires_at else None
    )


@router.get("/api-keys", response_model=List[APIKey])
async def list_api_keys(admin_user: AdminUser):
    """
    List all API keys (Admin only).

    Returns a list of all API keys (without the actual key values).
    """
    return list(API_KEYS_DB.values())


@router.delete("/api-keys/{key_id}")
async def revoke_api_key_endpoint(
    key_id: str,
    admin_user: AdminUser
):
    """
    Revoke an API key (Admin only).

    Disable an API key so it can no longer be used for authentication.
    """
    success = revoke_api_key(key_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API key {key_id} not found"
        )

    return {"message": f"API key {key_id} revoked successfully"}


@router.get("/roles")
async def list_roles(current_user: CurrentUser):
    """
    List all available roles.

    Returns all user roles and their associated permissions.
    """
    from lib.auth import ROLE_PERMISSIONS

    roles_info = []
    for role, permissions in ROLE_PERMISSIONS.items():
        roles_info.append({
            "role": role.value,
            "permissions": [p.value for p in permissions]
        })

    return {"roles": roles_info}
