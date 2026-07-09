import threading
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
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_concurrent_cancel_for_same_booking():
    """Sending 5 concurrent cancel requests for the same booking should only succeed once.
    The others should return 409 ALREADY_CANCELLED or 404/409 error.
    Importantly, only ONE RefundLog must be created in the database.
    """
    ts = datetime.now().timestamp()
    org = f"cancel-{ts}"
    headers = _register_and_login(org, "admin1")

    # Create room
    room = client.post(
        "/rooms",
        json={"name": "RoomC", "capacity": 10, "hourly_rate_cents": 1000},
        headers=headers,
    )
    room_id = room.json()["id"]

    # Create booking
    booking = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": _future(5), "end_time": _future(6)},
        headers=headers,
    )
    booking_id = booking.json()["id"]

    status_codes = []
    
    def cancel_request():
        resp = client.post(
            f"/bookings/{booking_id}/cancel",
            headers=headers,
        )
        status_codes.append(resp.status_code)

    threads = []
    for _ in range(5):
        t = threading.Thread(target=cancel_request)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    print("CONCURRENT CANCEL STATUS CODES:", status_codes)

    # Check database RefundLog count
    from app.database import SessionLocal
    from app.models import RefundLog
    db = SessionLocal()
    refund_logs = db.query(RefundLog).filter(RefundLog.booking_id == booking_id).all()
    db.close()

    print("REFUND LOGS IN DB COUNT:", len(refund_logs))
    
    # Assertions
    assert len(refund_logs) == 1, f"Duplicate RefundLogs created! Count: {len(refund_logs)}"
    assert status_codes.count(200) == 1, f"More than one success! Statuses: {status_codes}"


def test_malformed_datetime_booking():
    """Verify that sending a malformed datetime string returns a 400 INVALID_BOOKING_WINDOW error."""
    ts = datetime.now().timestamp()
    org = f"malformed-{ts}"
    headers = _register_and_login(org, "admin1")

    # Create room
    room = client.post(
        "/rooms",
        json={"name": "RoomM", "capacity": 10, "hourly_rate_cents": 1000},
        headers=headers,
    )
    room_id = room.json()["id"]

    # Create booking with malformed start_time
    resp = client.post(
        "/bookings",
        json={"room_id": room_id, "start_time": "invalid-date", "end_time": _future(6)},
        headers=headers,
    )
    print("MALFORMED DATETIME RESPONSE:", resp.status_code, resp.json())
    assert resp.status_code == 400
    assert resp.json()["code"] == "INVALID_BOOKING_WINDOW"

