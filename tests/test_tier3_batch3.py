import time
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _future(hours: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).replace(minute=0, second=0, microsecond=0).isoformat()


def _register_and_login(org: str, username: str, password: str = "pw12345"):
    client.post(
        "/auth/register",
        json={"org_name": org, "username": username, "password": password},
    )
    login = client.post(
        "/auth/login",
        json={"org_name": org, "username": username, "password": password},
    )
    data = login.json()
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    return headers


# --- Bug 1: Missing Usage Report Invalidation on Create Booking ---
def test_usage_report_cache_invalidation_on_create():
    """Usage report should immediately reflect a newly created booking."""
    ts = datetime.now().timestamp()
    org = f"cachecreate-{ts}"
    headers = _register_and_login(org, "admin1")

    # Create room
    room = client.post(
        "/rooms",
        json={"name": "ConfRoom", "capacity": 10, "hourly_rate_cents": 1000},
        headers=headers,
    )
    room_id = room.json()["id"]

    # Initial usage report request to populate cache
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_resp = client.get(
        f"/admin/usage-report?from={today_str}&to={today_str}",
        headers=headers,
    )
    assert report_resp.status_code == 200
    assert report_resp.json()["rooms"][0]["confirmed_bookings"] == 0

    # Now create a booking today
    start_time = (datetime.now(timezone.utc) + timedelta(hours=5)).strftime("%Y-%m-%dT%H:00:00+00:00")
    end_time = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime("%Y-%m-%dT%H:00:00+00:00")
    
    booking_resp = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": start_time, "end_time": end_time},
        headers=headers,
    )
    assert booking_resp.status_code == 201

    # Fetch usage report again - should return 1 confirmed booking, not 0 from cache
    report_resp2 = client.get(
        f"/admin/usage-report?from={today_str}&to={today_str}",
        headers=headers,
    )
    assert report_resp2.status_code == 200
    assert report_resp2.json()["rooms"][0]["confirmed_bookings"] == 1


# --- Bug 2: Missing Usage Report Invalidation on Create Room ---
def test_usage_report_cache_invalidation_on_create_room():
    """Usage report should immediately include a newly created room, even with 0 bookings."""
    ts = datetime.now().timestamp()
    org = f"cacheroom-{ts}"
    headers = _register_and_login(org, "admin1")

    # Create first room
    room1 = client.post(
        "/rooms",
        json={"name": "Room1", "capacity": 10, "hourly_rate_cents": 1000},
        headers=headers,
    )
    assert room1.status_code == 201

    # Fetch usage report to populate cache
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_resp = client.get(
        f"/admin/usage-report?from={today_str}&to={today_str}",
        headers=headers,
    )
    assert report_resp.status_code == 200
    assert len(report_resp.json()["rooms"]) == 1

    # Now create second room
    room2 = client.post(
        "/rooms",
        json={"name": "Room2", "capacity": 5, "hourly_rate_cents": 2000},
        headers=headers,
    )
    assert room2.status_code == 201

    # Fetch usage report again - should return 2 rooms, not 1 from cache
    report_resp2 = client.get(
        f"/admin/usage-report?from={today_str}&to={today_str}",
        headers=headers,
    )
    assert report_resp2.status_code == 200
    assert len(report_resp2.json()["rooms"]) == 2


# --- Bug 3: Missing Availability Invalidation on Cancel Booking ---
def test_availability_cache_invalidation_on_cancel():
    """Availability should immediately reflect booking cancellation (remove busy interval)."""
    ts = datetime.now().timestamp()
    org = f"cachecancel-{ts}"
    headers = _register_and_login(org, "admin1")

    # Create room
    room = client.post(
        "/rooms",
        json={"name": "ConfRoom", "capacity": 10, "hourly_rate_cents": 1000},
        headers=headers,
    )
    room_id = room.json()["id"]

    # Create booking
    start_dt = datetime.now(timezone.utc) + timedelta(hours=5)
    end_dt = datetime.now(timezone.utc) + timedelta(hours=6)
    start_time = start_dt.strftime("%Y-%m-%dT%H:00:00+00:00")
    end_time = end_dt.strftime("%Y-%m-%dT%H:00:00+00:00")
    
    booking_resp = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": start_time, "end_time": end_time},
        headers=headers,
    )
    assert booking_resp.status_code == 201
    booking_id = booking_resp.json()["id"]

    # Query availability to populate cache
    today_str = start_dt.strftime("%Y-%m-%d")
    avail_resp = client.get(
        f"/rooms/{room_id}/availability?date={today_str}",
        headers=headers,
    )
    assert avail_resp.status_code == 200
    assert len(avail_resp.json()["busy"]) == 1

    # Cancel booking
    cancel_resp = client.post(
        f"/bookings/{booking_id}/cancel",
        headers=headers,
    )
    assert cancel_resp.status_code == 200

    # Query availability again - should return empty busy list, not 1 item from cache
    avail_resp2 = client.get(
        f"/rooms/{room_id}/availability?date={today_str}",
        headers=headers,
    )
    assert avail_resp2.status_code == 200
    assert len(avail_resp2.json()["busy"]) == 0
