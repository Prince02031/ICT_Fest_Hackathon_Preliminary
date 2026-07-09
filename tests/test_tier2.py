"""Targeted tests for Tier 2 bug fixes."""
import math
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.timeutils import parse_input_datetime

client = TestClient(app)


def _future(hours: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).replace(minute=0, second=0, microsecond=0).isoformat()


def _register_and_login(org: str, username: str, password: str = "pw12345"):
    """Register a user and return (headers, user_info)."""
    reg = client.post(
        "/auth/register",
        json={"org_name": org, "username": username, "password": password},
    )
    assert reg.status_code == 201, reg.text
    login = client.post(
        "/auth/login",
        json={"org_name": org, "username": username, "password": password},
    )
    assert login.status_code == 200, login.text
    data = login.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    return headers, reg.json(), data


# --- Bug 10: Timezone parsing ---
def test_timezone_parsing_converts_to_utc():
    """Input with +05:00 offset should be stored as UTC (minus 5 hours)."""
    result = parse_input_datetime("2026-07-10T18:00:00+05:00")
    assert result == datetime(2026, 7, 10, 13, 0, 0)  # 18:00+05 = 13:00 UTC
    assert result.tzinfo is None  # stored naive


def test_timezone_parsing_naive_treated_as_utc():
    """Naive input should be kept as-is."""
    result = parse_input_datetime("2026-07-10T18:00:00")
    assert result == datetime(2026, 7, 10, 18, 0, 0)


# --- Bug 11: Token lifetime ---
def test_access_token_lifetime_is_900_seconds():
    """Access token exp - iat should be exactly 900 seconds."""
    import jwt
    from app.config import JWT_SECRET, JWT_ALGORITHM

    ts = datetime.now().timestamp()
    org = f"lifetime-{ts}"
    _, _, login_data = _register_and_login(org, "alice")
    token = login_data["access_token"]
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert payload["exp"] - payload["iat"] == 900  # 15 min = 900 sec


# --- Bug 12: Revocation checks jti ---
def test_logout_revokes_token():
    """After logout, the same access token should be rejected."""
    ts = datetime.now().timestamp()
    org = f"revoke-{ts}"
    headers, _, _ = _register_and_login(org, "alice")

    # Logout
    resp = client.post("/auth/logout", headers=headers)
    assert resp.status_code == 200

    # Subsequent request with same token should fail
    resp = client.get("/rooms", headers=headers)
    assert resp.status_code == 401


# --- Bug 13: Booking detail visibility ---
def test_member_cannot_see_other_members_booking():
    """A member should not be able to read another member's booking."""
    ts = datetime.now().timestamp()
    org = f"visibility-{ts}"

    # Admin creates room
    admin_headers, _, _ = _register_and_login(org, "admin1")
    room = client.post(
        "/rooms",
        json={"name": "Room A", "capacity": 4, "hourly_rate_cents": 1000},
        headers=admin_headers,
    )
    assert room.status_code == 201
    room_id = room.json()["id"]

    # Member 1 creates a booking
    m1_headers, _, _ = _register_and_login(org, "member1")
    booking = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": _future(50), "end_time": _future(52)},
        headers=m1_headers,
    )
    assert booking.status_code == 201
    booking_id = booking.json()["id"]

    # Member 1 can see their own booking
    resp = client.get(f"/bookings/{booking_id}", headers=m1_headers)
    assert resp.status_code == 200

    # Member 2 cannot see member 1's booking
    m2_headers, _, _ = _register_and_login(org, "member2")
    resp = client.get(f"/bookings/{booking_id}", headers=m2_headers)
    assert resp.status_code == 404

    # Admin CAN see member 1's booking
    resp = client.get(f"/bookings/{booking_id}", headers=admin_headers)
    assert resp.status_code == 200


# --- Bug 14: Refresh token single-use ---
def test_refresh_token_single_use():
    """A refresh token should only work once."""
    ts = datetime.now().timestamp()
    org = f"refresh-{ts}"
    _, _, login_data = _register_and_login(org, "alice")
    refresh_token = login_data["refresh_token"]

    # First refresh: should succeed
    resp1 = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp1.status_code == 200

    # Second refresh with same token: should fail
    resp2 = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp2.status_code == 401


# --- Bug 16: Refund rounding (half-cents round up) ---
def test_refund_rounding_half_cents_up():
    """50% of 1001 cents should be 501 (half-cent rounds up)."""
    ts = datetime.now().timestamp()
    org = f"rounding-{ts}"

    admin_headers, _, _ = _register_and_login(org, "admin1")
    # Create room with 1001 cents/hour
    room = client.post(
        "/rooms",
        json={"name": "Odd Room", "capacity": 2, "hourly_rate_cents": 1001},
        headers=admin_headers,
    )
    assert room.status_code == 201
    room_id = room.json()["id"]

    # Create booking for 1 hour (price = 1001 cents), far enough for 50% refund
    start = _future(30)  # 30h from now → notice ≥ 24h, < 48h → 50%
    end_time = (
        datetime.now(timezone.utc) + timedelta(hours=31)
    ).replace(minute=0, second=0, microsecond=0).isoformat()

    booking = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": start, "end_time": end_time},
        headers=admin_headers,
    )
    assert booking.status_code == 201
    booking_id = booking.json()["id"]
    assert booking.json()["price_cents"] == 1001

    cancel = client.post(f"/bookings/{booking_id}/cancel", headers=admin_headers)
    assert cancel.status_code == 200
    assert cancel.json()["refund_percent"] == 50
    assert cancel.json()["refund_amount_cents"] == 501  # NOT 500
