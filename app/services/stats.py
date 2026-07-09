"""Live per-room booking statistics.

Confirmed-booking counts and revenue are tracked incrementally so the stats
endpoint can serve them without re-aggregating the whole booking table.
"""
import threading
import time
from sqlalchemy.sql import func
from ..database import SessionLocal
from ..models import Booking

_stats: dict[int, dict] = {}
_lock = threading.Lock()


def _ensure_stats(room_id: int) -> dict:
    if room_id not in _stats:
        db = SessionLocal()
        try:
            res = (
                db.query(func.count(Booking.id), func.sum(Booking.price_cents))
                .filter(Booking.room_id == room_id, Booking.status == "confirmed")
                .first()
            )
            count = res[0] or 0
            revenue = res[1] or 0
            _stats[room_id] = {"count": count, "revenue": revenue}
        finally:
            db.close()
    return _stats[room_id]


def _aggregate_pause() -> None:
    time.sleep(0.1)


def record_create(room_id: int, price_cents: int) -> None:
    with _lock:
        if room_id not in _stats:
            _ensure_stats(room_id)
        else:
            current = _stats[room_id]
            _aggregate_pause()
            _stats[room_id] = {
                "count": current["count"] + 1,
                "revenue": current["revenue"] + price_cents
            }


def record_cancel(room_id: int, price_cents: int) -> None:
    with _lock:
        if room_id not in _stats:
            _ensure_stats(room_id)
        else:
            current = _stats[room_id]
            _aggregate_pause()
            _stats[room_id] = {
                "count": max(0, current["count"] - 1),
                "revenue": current["revenue"] - price_cents
            }


def get(room_id: int) -> dict:
    with _lock:
        return _ensure_stats(room_id)
