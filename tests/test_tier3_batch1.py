import threading
import time
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app
from app.services.notifications import notify_created, notify_cancelled
from app.services.reference import next_reference_code

client = TestClient(app)

# Helper function to get future ISO timestamp
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


# --- Bug 1: Deadlock in notifications ---
class DummyBooking:
    def __init__(self):
        self.id = 1
        self.room_id = 1
        self.price_cents = 1000

def test_notification_deadlock():
    """Spin up two threads calling notify_created and notify_cancelled.
    Without the fix, they will likely deadlock (hang). We will set a timeout.
    """
    booking = DummyBooking()
    
    t1_started = threading.Event()
    t2_started = threading.Event()
    
    def run_created():
        t1_started.set()
        notify_created(booking)
        
    def run_cancelled():
        t2_started.set()
        notify_cancelled(booking)

    t1 = threading.Thread(target=run_created)
    t2 = threading.Thread(target=run_cancelled)
    
    t1.start()
    t2.start()
    
    # Wait for threads to finish with a 1.0 second timeout.
    # Because each has a sleep of 0.1s - 0.12s, they should normally finish in under 0.5s.
    t1.join(timeout=1.0)
    t2.join(timeout=1.0)
    
    deadlocked = t1.is_alive() or t2.is_alive()
    assert not deadlocked, "Deadlock occurred in notification locks!"


# --- Bug 2: Back-to-back booking overlap ---
def test_back_to_back_bookings():
    """Booking B starts exactly when Booking A ends. This should be allowed."""
    ts = datetime.now().timestamp()
    org = f"backtoback-{ts}"
    headers = _register_and_login(org, "admin1")

    # Create room
    room = client.post(
        "/rooms",
        json={"name": "ConfRoom", "capacity": 10, "hourly_rate_cents": 1000},
        headers=headers,
    )
    assert room.status_code == 201
    room_id = room.json()["id"]

    # Booking A: 12:00 to 13:00 (e.g. +5 hours to +6 hours from now)
    start_a = _future(5)
    end_a = _future(6)
    
    # Booking B: 13:00 to 14:00 (e.g. +6 hours to +7 hours from now)
    start_b = _future(6)
    end_b = _future(7)

    # Create booking A
    resp_a = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": start_a, "end_time": end_a},
        headers=headers,
    )
    assert resp_a.status_code == 201

    # Create booking B (back-to-back)
    resp_b = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": start_b, "end_time": end_b},
        headers=headers,
    )
    # Expected: 201 (success), Actual: 409 (conflict)
    assert resp_b.status_code == 201, f"Failed to book back-to-back: {resp_b.json()}"


# --- Bug 3: Reference code generator race condition ---
def test_reference_code_concurrency():
    """Multiple concurrent calls to next_reference_code should yield unique reference codes."""
    codes = []
    
    def get_code():
        codes.append(next_reference_code())

    threads = []
    for _ in range(5):
        t = threading.Thread(target=get_code)
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    # Expected: 5 unique codes. Actual: duplicates because of race condition.
    assert len(set(codes)) == 5, f"Duplicate reference codes generated: {codes}"
