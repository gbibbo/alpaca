"""
Authentication and Authorization System
Epic 3 - API/Auth & WebSocket

Implements JWT-based authentication with role-based access control (RBAC).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, List
from enum import Enum
import secrets
import hashlib

from pydantic import BaseModel, Field
from jose import JWTError, jwt
from passlib.context import CryptContext


# Configuration
SECRET_KEY = secrets.token_urlsafe(32)  # In production, use env variable
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class UserRole(str, Enum):
    """User roles for RBAC."""
    ADMIN = "admin"
    TRADER = "trader"
    VIEWER = "viewer"
    API_CLIENT = "api_client"


class Permission(str, Enum):
    """Granular permissions."""
    READ_SIGNALS = "read:signals"
    WRITE_SIGNALS = "write:signals"
    READ_ORDERS = "read:orders"
    WRITE_ORDERS = "write:orders"
    READ_POSITIONS = "read:positions"
    READ_METRICS = "read:metrics"
    WRITE_METRICS = "write:metrics"
    MANAGE_USERS = "manage:users"
    EMERGENCY_STOP = "emergency:stop"
    READ_BACKTEST = "read:backtest"
    WRITE_BACKTEST = "write:backtest"


# Role-Permission mapping
ROLE_PERMISSIONS = {
    UserRole.ADMIN: [
        Permission.READ_SIGNALS,
        Permission.WRITE_SIGNALS,
        Permission.READ_ORDERS,
        Permission.WRITE_ORDERS,
        Permission.READ_POSITIONS,
        Permission.READ_METRICS,
        Permission.WRITE_METRICS,
        Permission.MANAGE_USERS,
        Permission.EMERGENCY_STOP,
        Permission.READ_BACKTEST,
        Permission.WRITE_BACKTEST,
    ],
    UserRole.TRADER: [
        Permission.READ_SIGNALS,
        Permission.WRITE_SIGNALS,
        Permission.READ_ORDERS,
        Permission.WRITE_ORDERS,
        Permission.READ_POSITIONS,
        Permission.READ_METRICS,
        Permission.READ_BACKTEST,
        Permission.WRITE_BACKTEST,
    ],
    UserRole.VIEWER: [
        Permission.READ_SIGNALS,
        Permission.READ_ORDERS,
        Permission.READ_POSITIONS,
        Permission.READ_METRICS,
        Permission.READ_BACKTEST,
    ],
    UserRole.API_CLIENT: [
        Permission.READ_SIGNALS,
        Permission.READ_ORDERS,
        Permission.READ_POSITIONS,
        Permission.READ_METRICS,
        Permission.READ_BACKTEST,
    ],
}


class User(BaseModel):
    """User model."""
    username: str
    email: str
    full_name: Optional[str] = None
    role: UserRole
    disabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login: Optional[datetime] = None


class UserInDB(User):
    """User model with hashed password (for DB storage)."""
    hashed_password: str


class Token(BaseModel):
    """Access token response."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: Optional[str] = None


class TokenData(BaseModel):
    """Token payload data."""
    username: str
    role: UserRole
    permissions: List[Permission]
    exp: datetime


class APIKey(BaseModel):
    """API Key for machine-to-machine authentication."""
    key_id: str
    key_hash: str
    name: str
    role: UserRole
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    disabled: bool = False
    last_used: Optional[datetime] = None


# Password hashing
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return pwd_context.hash(password)


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None
) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(username: str) -> str:
    """Create a refresh token."""
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {
        "sub": username,
        "type": "refresh",
        "exp": expire
    }
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[TokenData]:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        role: str = payload.get("role")
        permissions: List[str] = payload.get("permissions", [])
        exp: int = payload.get("exp")

        if username is None or role is None:
            return None

        exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)

        return TokenData(
            username=username,
            role=UserRole(role),
            permissions=[Permission(p) for p in permissions],
            exp=exp_dt
        )
    except JWTError:
        return None


def get_user_permissions(role: UserRole) -> List[Permission]:
    """Get permissions for a role."""
    return ROLE_PERMISSIONS.get(role, [])


def has_permission(user_role: UserRole, required_permission: Permission) -> bool:
    """Check if a role has a specific permission."""
    permissions = get_user_permissions(user_role)
    return required_permission in permissions


def generate_api_key() -> tuple[str, str]:
    """
    Generate a new API key.

    Returns:
        Tuple of (key_id, api_key)
        - key_id: Public identifier
        - api_key: Secret key (show once to user)
    """
    key_id = f"ak_{secrets.token_urlsafe(16)}"
    api_key = secrets.token_urlsafe(32)
    return key_id, api_key


def hash_api_key(api_key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def verify_api_key(api_key: str, key_hash: str) -> bool:
    """Verify an API key against its hash."""
    return hashlib.sha256(api_key.encode()).hexdigest() == key_hash


# In-memory user database (in production, use real DB)
# Use lazy initialization to avoid bcrypt/argon2 issues at import time
USERS_DB: dict[str, UserInDB] = {}


def _init_default_users():
    """Initialize default users if not already present."""
    if not USERS_DB:
        USERS_DB["admin"] = UserInDB(
            username="admin",
            email="admin@trading.com",
            full_name="System Administrator",
            role=UserRole.ADMIN,
            hashed_password=get_password_hash("admin123"),  # Change in production!
        )
        USERS_DB["trader1"] = UserInDB(
            username="trader1",
            email="trader1@trading.com",
            full_name="Main Trader",
            role=UserRole.TRADER,
            hashed_password=get_password_hash("trader123"),
        )
        USERS_DB["viewer1"] = UserInDB(
            username="viewer1",
            email="viewer1@trading.com",
            full_name="Portfolio Viewer",
            role=UserRole.VIEWER,
            hashed_password=get_password_hash("viewer123"),
        )

# In-memory API keys database
API_KEYS_DB: dict[str, APIKey] = {}


def authenticate_user(username: str, password: str) -> Optional[UserInDB]:
    """Authenticate a user by username and password."""
    _init_default_users()  # Ensure default users are loaded
    user = USERS_DB.get(username)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    if user.disabled:
        return None
    return user


def authenticate_api_key(api_key: str) -> Optional[APIKey]:
    """Authenticate using an API key."""
    # Check all API keys
    for key_id, stored_key in API_KEYS_DB.items():
        if stored_key.disabled:
            continue

        # Check expiration
        if stored_key.expires_at:
            if datetime.now(timezone.utc) > stored_key.expires_at:
                continue

        # Verify key
        if verify_api_key(api_key, stored_key.key_hash):
            # Update last used
            stored_key.last_used = datetime.now(timezone.utc)
            return stored_key

    return None


def create_user(
    username: str,
    email: str,
    password: str,
    role: UserRole,
    full_name: Optional[str] = None
) -> UserInDB:
    """Create a new user."""
    if username in USERS_DB:
        raise ValueError(f"User {username} already exists")

    user = UserInDB(
        username=username,
        email=email,
        full_name=full_name,
        role=role,
        hashed_password=get_password_hash(password)
    )

    USERS_DB[username] = user
    return user


def create_api_key_for_client(
    name: str,
    role: UserRole,
    expires_in_days: Optional[int] = None
) -> tuple[APIKey, str]:
    """
    Create a new API key for a client.

    Returns:
        Tuple of (APIKey object, plain_api_key)
        The plain API key should be shown to user once and then discarded.
    """
    key_id, api_key = generate_api_key()
    key_hash = hash_api_key(api_key)

    expires_at = None
    if expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    api_key_obj = APIKey(
        key_id=key_id,
        key_hash=key_hash,
        name=name,
        role=role,
        expires_at=expires_at
    )

    API_KEYS_DB[key_id] = api_key_obj
    return api_key_obj, api_key


def revoke_api_key(key_id: str) -> bool:
    """Revoke (disable) an API key."""
    if key_id in API_KEYS_DB:
        API_KEYS_DB[key_id].disabled = True
        return True
    return False


def get_user(username: str) -> Optional[User]:
    """Get user by username (without password hash)."""
    _init_default_users()  # Ensure default users are loaded
    user_db = USERS_DB.get(username)
    if user_db:
        return User(**user_db.model_dump(exclude={"hashed_password"}))
    return None
