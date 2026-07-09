import threading
import time
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.services.stats import _stats

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


# --- Bug 1: Rate Limiter Race Condition ---
def test_rate_limiter_concurrency():
    """Sending 25 concurrent booking requests should trigger 429 RATE_LIMITED.
    Without a lock, concurrent requests overlap during time.sleep and bypass the limit.
    """
    ts = datetime.now().timestamp()
    org = f"ratelimit-{ts}"
    headers = _register_and_login(org, "alice")

    # Create a room
    room = client.post(
        "/rooms",
        json={"name": "RoomRL", "capacity": 10, "hourly_rate_cents": 1000},
        headers=headers,
    )
    room_id = room.json()["id"]

    status_codes = []
    
    # We will send 25 concurrent requests to POST /bookings
    # They should trigger RATE_LIMITED since max is 20 per 60 seconds
    def make_request(index):
        start = _future(index + 5)
        end = _future(index + 6)
        resp = client.post(
            "/bookings",
            json={"room_id": room_id, "start_time": start, "end_time": end},
            headers=headers,
        )
        status_codes.append(resp.status_code)

    threads = []
    for i in range(25):
        t = threading.Thread(target=make_request, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Expected: At least some 429s (exactly 5 should fail with 429 if 20 succeeded).
    # Actual without lock: All 25 might succeed (201) or count will be way below 20.
    rate_limited_count = status_codes.count(429)
    assert rate_limited_count > 0, f"Rate limiting failed to trigger! Status codes: {status_codes}"


# --- Bug 2: Room Stats Concurrency Race ---
def test_room_stats_concurrency():
    """Creating 5 concurrent bookings should correctly increment room stats total count and revenue."""
    ts = datetime.now().timestamp()
    org = f"statsrace-{ts}"
    headers = _register_and_login(org, "alice")

    # Create a room
    room = client.post(
        "/rooms",
        json={"name": "RoomStats", "capacity": 10, "hourly_rate_cents": 1000},
        headers=headers,
    )
    room_id = room.json()["id"]

    responses = []
    def make_booking(index):
        start = _future(index + 5)
        end = _future(index + 6)
        resp = client.post(
            "/bookings",
            json={"room_id": room_id, "start_time": start, "end_time": end},
            headers=headers,
        )
        responses.append((resp.status_code, resp.json() if resp.status_code != 500 else "500"))

    threads = []
    for i in range(5):
        t = threading.Thread(target=make_booking, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print("CONCURRENT BOOKING RESPONSES:", responses)

    # Get stats
    stats_resp = client.get(f"/rooms/{room_id}/stats", headers=headers)
    assert stats_resp.status_code == 200
    data = stats_resp.json()
    
    # Query database directly to count bookings for room_id
    from app.database import SessionLocal
    from app.models import Booking
    db = SessionLocal()
    bookings_in_db = db.query(Booking).filter(Booking.room_id == room_id).all()
    print("BOOKINGS IN DB FOR ROOM:", len(bookings_in_db), [(b.id, b.start_time, b.status) for b in bookings_in_db])
    db.close()
    
    # Expected: total_confirmed_bookings = 5, total_revenue_cents = 5000.
    # Actual: count is likely less than 5 because of race condition in stats recording.
    assert data["total_confirmed_bookings"] == 5, f"Stats count mismatch! Data: {data}"
    assert data["total_revenue_cents"] == 5000, f"Stats revenue mismatch! Data: {data}"


# --- Bug 3: Stats Loss on Server Restart ---
def test_stats_recovery_on_restart():
    """Simulate server restart by clearing the in-memory stats cache, verifying recovery from DB."""
    ts = datetime.now().timestamp()
    org = f"statsrestart-{ts}"
    headers = _register_and_login(org, "alice")

    # Create a room
    room = client.post(
        "/rooms",
        json={"name": "RestartRoom", "capacity": 10, "hourly_rate_cents": 1500},
        headers=headers,
    )
    room_id = room.json()["id"]

    # Create a booking (price should be 1500)
    resp = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": _future(10), "end_time": _future(11)},
        headers=headers,
    )
    assert resp.status_code == 201

    # Check stats initially
    stats_resp = client.get(f"/rooms/{room_id}/stats", headers=headers)
    
    from app.database import SessionLocal
    from app.models import Booking
    db = SessionLocal()
    bookings_in_db = db.query(Booking).filter(Booking.room_id == room_id).all()
    print("RESTART TEST BOOKINGS IN DB:", len(bookings_in_db), [(b.id, b.start_time, b.status) for b in bookings_in_db])
    db.close()
    
    assert stats_resp.json()["total_confirmed_bookings"] == 1
    assert stats_resp.json()["total_revenue_cents"] == 1500

    # Simulate server restart: clear in-memory _stats dict
    _stats.clear()

    # Check stats again: should recover the correct stats from DB
    stats_resp = client.get(f"/rooms/{room_id}/stats", headers=headers)
    assert stats_resp.status_code == 200
    data = stats_resp.json()
    assert data["total_confirmed_bookings"] == 1, f"Stats lost on restart! Data: {data}"
    assert data["total_revenue_cents"] == 1500, f"Revenue lost on restart! Data: {data}"
