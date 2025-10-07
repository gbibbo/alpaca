"""
Epic 3 - Authentication Tests

Tests for JWT authentication, RBAC, and API key management.
"""

import pytest
from datetime import datetime, timezone, timedelta

from lib.auth import (
    User,
    UserRole,
    Permission,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
    get_password_hash,
    has_permission,
    get_user_permissions,
    authenticate_user,
    create_user,
    generate_api_key,
    hash_api_key,
    verify_api_key,
    create_api_key_for_client,
    authenticate_api_key,
    revoke_api_key,
    USERS_DB,
    API_KEYS_DB,
)


class TestPasswordHashing:
    """Test password hashing functionality."""

    def test_password_hashing(self):
        """Test password can be hashed and verified."""
        password = "test_password123"
        hashed = get_password_hash(password)

        assert hashed != password
        assert verify_password(password, hashed)

    def test_wrong_password_fails(self):
        """Test wrong password fails verification."""
        password = "correct_password"
        wrong_password = "wrong_password"
        hashed = get_password_hash(password)

        assert not verify_password(wrong_password, hashed)

    def test_same_password_different_hashes(self):
        """Test same password produces different hashes (salt)."""
        password = "test_password"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)

        assert hash1 != hash2
        assert verify_password(password, hash1)
        assert verify_password(password, hash2)


class TestJWTTokens:
    """Test JWT token creation and validation."""

    def test_create_access_token(self):
        """Test access token creation."""
        data = {"sub": "testuser", "role": "trader"}
        token = create_access_token(data)

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_decode_valid_token(self):
        """Test decoding a valid token."""
        permissions = [Permission.READ_SIGNALS, Permission.WRITE_SIGNALS]
        data = {
            "sub": "testuser",
            "role": UserRole.TRADER.value,
            "permissions": [p.value for p in permissions]
        }
        token = create_access_token(data)

        token_data = decode_token(token)

        assert token_data is not None
        assert token_data.username == "testuser"
        assert token_data.role == UserRole.TRADER
        assert Permission.READ_SIGNALS in token_data.permissions

    def test_decode_invalid_token(self):
        """Test decoding an invalid token returns None."""
        invalid_token = "invalid.token.string"
        token_data = decode_token(invalid_token)

        assert token_data is None

    def test_token_expiration(self):
        """Test token with short expiration."""
        data = {"sub": "testuser", "role": "trader"}
        token = create_access_token(data, expires_delta=timedelta(seconds=1))

        # Should be valid immediately
        token_data = decode_token(token)
        assert token_data is not None

        # TODO: Test expiration (would need to wait or mock time)

    def test_refresh_token_creation(self):
        """Test refresh token creation."""
        token = create_refresh_token("testuser")

        assert token is not None
        assert isinstance(token, str)


class TestRBAC:
    """Test Role-Based Access Control."""

    def test_admin_has_all_permissions(self):
        """Test admin role has all permissions."""
        permissions = get_user_permissions(UserRole.ADMIN)

        assert Permission.READ_SIGNALS in permissions
        assert Permission.WRITE_SIGNALS in permissions
        assert Permission.MANAGE_USERS in permissions
        assert Permission.EMERGENCY_STOP in permissions

    def test_trader_permissions(self):
        """Test trader role permissions."""
        permissions = get_user_permissions(UserRole.TRADER)

        assert Permission.READ_SIGNALS in permissions
        assert Permission.WRITE_SIGNALS in permissions
        assert Permission.READ_ORDERS in permissions
        assert Permission.WRITE_ORDERS in permissions
        # Should not have admin permissions
        assert Permission.MANAGE_USERS not in permissions

    def test_viewer_permissions(self):
        """Test viewer role permissions."""
        permissions = get_user_permissions(UserRole.VIEWER)

        assert Permission.READ_SIGNALS in permissions
        assert Permission.READ_ORDERS in permissions
        assert Permission.READ_POSITIONS in permissions
        # Should not have write permissions
        assert Permission.WRITE_SIGNALS not in permissions
        assert Permission.WRITE_ORDERS not in permissions

    def test_has_permission_check(self):
        """Test permission checking."""
        assert has_permission(UserRole.ADMIN, Permission.MANAGE_USERS)
        assert has_permission(UserRole.TRADER, Permission.WRITE_SIGNALS)
        assert not has_permission(UserRole.VIEWER, Permission.WRITE_SIGNALS)


class TestUserManagement:
    """Test user management functionality."""

    def test_authenticate_valid_user(self):
        """Test authenticating a valid user."""
        # Using default admin user
        user = authenticate_user("admin", "admin123")

        assert user is not None
        assert user.username == "admin"
        assert user.role == UserRole.ADMIN

    def test_authenticate_invalid_password(self):
        """Test authentication fails with wrong password."""
        user = authenticate_user("admin", "wrong_password")

        assert user is None

    def test_authenticate_nonexistent_user(self):
        """Test authentication fails for nonexistent user."""
        user = authenticate_user("nonexistent", "password")

        assert user is None

    def test_create_new_user(self):
        """Test creating a new user."""
        username = f"newuser_{datetime.now().timestamp()}"

        user = create_user(
            username=username,
            email=f"{username}@test.com",
            password="test123",
            role=UserRole.TRADER,
            full_name="Test User"
        )

        assert user.username == username
        assert user.email == f"{username}@test.com"
        assert user.role == UserRole.TRADER
        assert user.full_name == "Test User"

        # Verify user can authenticate
        auth_user = authenticate_user(username, "test123")
        assert auth_user is not None
        assert auth_user.username == username

        # Cleanup
        del USERS_DB[username]

    def test_create_duplicate_user_fails(self):
        """Test creating duplicate user fails."""
        username = f"duplicate_{datetime.now().timestamp()}"

        create_user(
            username=username,
            email=f"{username}@test.com",
            password="test123",
            role=UserRole.TRADER
        )

        # Try to create again
        with pytest.raises(ValueError):
            create_user(
                username=username,
                email=f"{username}@test.com",
                password="test123",
                role=UserRole.TRADER
            )

        # Cleanup
        del USERS_DB[username]


class TestAPIKeys:
    """Test API key functionality."""

    def test_generate_api_key(self):
        """Test API key generation."""
        key_id, api_key = generate_api_key()

        assert key_id.startswith("ak_")
        assert len(api_key) > 20  # Should be sufficiently long

    def test_hash_and_verify_api_key(self):
        """Test API key hashing and verification."""
        _, api_key = generate_api_key()
        key_hash = hash_api_key(api_key)

        assert key_hash != api_key
        assert verify_api_key(api_key, key_hash)

    def test_wrong_api_key_fails(self):
        """Test wrong API key fails verification."""
        _, api_key = generate_api_key()
        _, wrong_key = generate_api_key()
        key_hash = hash_api_key(api_key)

        assert not verify_api_key(wrong_key, key_hash)

    def test_create_api_key_for_client(self):
        """Test creating an API key for a client."""
        api_key_obj, api_key = create_api_key_for_client(
            name="Test Client",
            role=UserRole.API_CLIENT,
            expires_in_days=30
        )

        assert api_key_obj.name == "Test Client"
        assert api_key_obj.role == UserRole.API_CLIENT
        assert api_key_obj.expires_at is not None
        assert api_key is not None

        # Verify key is in database
        assert api_key_obj.key_id in API_KEYS_DB

        # Cleanup
        del API_KEYS_DB[api_key_obj.key_id]

    def test_authenticate_with_api_key(self):
        """Test authentication using API key."""
        api_key_obj, api_key = create_api_key_for_client(
            name="Auth Test",
            role=UserRole.API_CLIENT
        )

        # Authenticate with API key
        authenticated = authenticate_api_key(api_key)

        assert authenticated is not None
        assert authenticated.key_id == api_key_obj.key_id
        assert authenticated.name == "Auth Test"
        assert authenticated.last_used is not None

        # Cleanup
        del API_KEYS_DB[api_key_obj.key_id]

    def test_revoke_api_key(self):
        """Test revoking an API key."""
        api_key_obj, api_key = create_api_key_for_client(
            name="Revoke Test",
            role=UserRole.API_CLIENT
        )

        # Revoke the key
        success = revoke_api_key(api_key_obj.key_id)
        assert success

        # Try to authenticate with revoked key
        authenticated = authenticate_api_key(api_key)
        assert authenticated is None  # Should fail

        # Cleanup
        del API_KEYS_DB[api_key_obj.key_id]

    def test_expired_api_key_fails(self):
        """Test expired API key fails authentication."""
        api_key_obj, api_key = create_api_key_for_client(
            name="Expired Test",
            role=UserRole.API_CLIENT,
            expires_in_days=0  # Expires immediately
        )

        # Set expiration to past
        api_key_obj.expires_at = datetime.now(timezone.utc) - timedelta(days=1)

        # Try to authenticate
        authenticated = authenticate_api_key(api_key)
        assert authenticated is None  # Should fail

        # Cleanup
        del API_KEYS_DB[api_key_obj.key_id]


class TestUserModel:
    """Test User model."""

    def test_user_model_creation(self):
        """Test creating a User model."""
        user = User(
            username="testuser",
            email="test@example.com",
            role=UserRole.TRADER,
            full_name="Test User"
        )

        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.role == UserRole.TRADER
        assert user.full_name == "Test User"
        assert not user.disabled
        assert user.created_at is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
