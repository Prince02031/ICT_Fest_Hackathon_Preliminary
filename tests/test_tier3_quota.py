import threading
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def _future(hours: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).replace(minute=0, second=0, microsecond=0).isoformat()


def _register_and_login_admin_and_member(org: str):
    # Register and login admin
    client.post(
        "/auth/register",
        json={"org_name": org, "username": "admin1", "password": "pw12345"},
    )
    login_a = client.post(
        "/auth/login",
        json={"org_name": org, "username": "admin1", "password": "pw12345"},
    )
    admin_headers = {"Authorization": f"Bearer {login_a.json()['access_token']}"}

    # Register and login member
    client.post(
        "/auth/register",
        json={"org_name": org, "username": "member1", "password": "pw12345"},
    )
    login_m = client.post(
        "/auth/login",
        json={"org_name": org, "username": "member1", "password": "pw12345"},
    )
    member_headers = {"Authorization": f"Bearer {login_m.json()['access_token']}"}
    
    return admin_headers, member_headers


def test_concurrent_quota_violation():
    """Sending 4 concurrent booking requests for a member should enforce the quota limit of 3.
    Without synchronization, multiple threads might check quota concurrently, see < 3, and all succeed.
    """
    ts = datetime.now().timestamp()
    org = f"quota-{ts}"
    admin_headers, member_headers = _register_and_login_admin_and_member(org)

    # Create room
    room = client.post(
        "/rooms",
        json={"name": "RoomQ", "capacity": 10, "hourly_rate_cents": 1000},
        headers=admin_headers,
    )
    assert room.status_code == 201
    room_id = room.json()["id"]

    status_codes = []
    
    def make_booking(index):
        # Book different hours so they don't conflict on time overlap
        # index+1 to index+2, index+3 to index+4, etc.
        start = _future(index * 2 + 1)
        end = _future(index * 2 + 2)
        resp = client.post(
            "/bookings",
            json={"room_id": room_id, "start_time": start, "end_time": end},
            headers=member_headers,
        )
        status_codes.append(resp.status_code)

    threads = []
    for i in range(4):
        t = threading.Thread(target=make_booking, args=(i,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # Expected: At least one request fails with 409 QUOTA_EXCEEDED (i.e. status code 409)
    # Actual: All 4 might succeed (status code 201) due to the race condition in quota check
    quota_exceeded_count = status_codes.count(409)
    success_count = status_codes.count(201)
    
    print("CONCURRENT QUOTA STATUS CODES:", status_codes)
    assert quota_exceeded_count >= 1, f"Quota was bypassed! Successes: {success_count}, Failures: {status_codes}"
