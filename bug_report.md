# CoWork Bug Report — Preliminary Round Fixes

This report documents every bug identified and resolved in the CoWork codebase during the preliminary round. In accordance with Section 10 of the Problem Statement, each entry details the location of the bug, the root cause, and the implemented fix.

---

## 1. Pagination Offset Calculation Bug
* **Location:** `app/routers/bookings.py`
* **Bug:** The query offset was calculated as `page * limit`, causing the API to skip the first page of results entirely.
* **Fix:** Corrected the offset calculation to `(page - 1) * limit`.

## 2. Pagination Limit Hardcoding
* **Location:** `app/routers/bookings.py`
* **Bug:** The result limit was hardcoded to `10`, ignoring the custom `limit` parameter supplied in the query string.
* **Fix:** Replaced the hardcoded value with the dynamic `limit` parameter.

## 3. Booking List Sort Order
* **Location:** `app/routers/bookings.py`
* **Bug:** Bookings were ordered descending (`.desc()`) by `start_time` and had no tie-breaker, violating the ascending sorting requirement.
* **Fix:** Changed the sort to ascending and added `id.asc()` as the secondary tie-breaker: `.order_by(Booking.start_time.asc(), Booking.id.asc())`.

## 4. Booking Detail start_time Overwrite
* **Location:** `app/routers/bookings.py`
* **Bug:** The booking detail response payload was overwriting the `start_time` key with the value of `created_at`.
* **Fix:** Corrected the serialization logic to map `start_time` to the actual `start_time` datetime object.

## 5. Duplicate Username Registration Response
* **Location:** `app/routers/auth.py`
* **Bug:** Registering a duplicate username within the same organization did not raise a conflict, failing to return `409 USERNAME_TAKEN`.
* **Fix:** Added a database check for existing users matching the username and organization ID, raising `AppError(409, "USERNAME_TAKEN")` when found.

## 6. Notice Period Threshold for <24h Cancellations
* **Location:** `app/routers/bookings.py`
* **Bug:** Notice periods of less than 24 hours returned a 50% refund instead of the 0% refund mandated by Section 4, Rule 6.
* **Fix:** Corrected the cancellation conditional checks to set the refund percentage to `0` when notice is under 24 hours.

## 7. Notice Period Threshold for >=48h Cancellations
* **Location:** `app/routers/bookings.py`
* **Bug:** Notice periods of exactly 48 hours were processed in the 50% refund tier instead of 100% due to using strict inequality (`>`) instead of greater-than-or-equal-to (`>=`).
* **Fix:** Corrected the boundary check to `>= timedelta(hours=48)`.

## 8. Past Booking Grace Window
* **Location:** `app/routers/bookings.py`
* **Bug:** Allowed bookings starting in the past within a grace window of 5 minutes.
* **Fix:** Removed the grace window and enforced that `start_time` must be strictly in the future (`start <= now` raises `INVALID_BOOKING_WINDOW`).

## 9. Minimum Duration Validation
* **Location:** `app/routers/bookings.py`
* **Bug:** The API failed to validate the minimum booking duration of 1 hour.
* **Fix:** Added a duration validation check: `duration_hours < MIN_DURATION_HOURS` raises `INVALID_BOOKING_WINDOW` (400).

## 10. Timezone Parsing and Offsets
* **Location:** `app/timeutils.py`
* **Bug:** Naive parsing of date/time inputs stripped timezone offsets without shifting the underlying datetime value to UTC, resulting in incorrect stored times.
* **Fix:** Ensured timezone-aware inputs are explicitly converted to UTC before removing the timezone info for database storage: `.astimezone(timezone.utc).replace(tzinfo=None)`.

## 11. Access Token Lifetime Math Error
* **Location:** `app/auth.py`
* **Bug:** Access tokens were issued with a lifetime of 15 hours instead of exactly 15 minutes (900 seconds) due to a multiplication bug.
* **Fix:** Corrected token payload calculations to assign `exp` using `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)` where `ACCESS_TOKEN_EXPIRE_MINUTES = 15`.

## 12. Session Revocation Identifier Mismatch
* **Location:** `app/auth.py`
* **Bug:** Access token revocation checked the subject (`sub` user ID) instead of the unique token identifier (`jti`), logging out the user from all devices instead of only the active session.
* **Fix:** Changed the revocation and validation routines to blacklist and lookup the unique token `jti`.

## 13. Booking Detail Visibility Leak
* **Location:** `app/routers/bookings.py`
* **Bug:** Members could view bookings belonging to other members by guessing their booking ID.
* **Fix:** Added verification logic to ensure non-admin users can only retrieve bookings they own, raising `404 BOOKING_NOT_FOUND` otherwise.

## 14. Refresh Token Single-Use Invalidation
* **Location:** `app/routers/auth.py` & `app/auth.py`
* **Bug:** Refresh tokens were not marked as consumed upon use, allowing malicious reuse and replay.
* **Fix:** Added thread-safe consumption tracking `try_consume_token(jti)` to ensure refresh tokens are invalidated immediately upon their first use.

## 15. Cross-Tenant CSV Export Leak
* **Location:** `app/services/export.py`
* **Bug:** Administrators could export booking data from other organizations by passing external room IDs.
* **Fix:** Enforced strict organization-level scoping within the database query inside `_fetch_scoped`.

## 16. Refund Calculation Rounding
* **Location:** `app/services/refunds.py`
* **Bug:** Half-cents were rounded down instead of up during refund calculation.
* **Fix:** Updated the rounding calculation to use the integer-division half-up formula: `(price_cents * percent + 50) // 100`.

## 17. Notification Lock Deadlock
* **Location:** `app/services/notifications.py`
* **Bug:** Concurrent creation and cancellation requests caused deadlocks because `notify_created` and `notify_cancelled` acquired `_email_lock` and `_audit_lock` in opposite orders.
* **Fix:** Refactored lock acquisitions to run sequentially rather than nested, removing any circular wait state and preventing deadlocks.

## 18. Booking End Time Validation
* **Location:** `app/routers/bookings.py`
* **Bug:** The API allowed bookings where the end time was equal to or prior to the start time (`end_time <= start_time`).
* **Fix:** Ensured non-positive and zero-hour durations are validated and rejected with HTTP 400 `INVALID_BOOKING_WINDOW`.

## 19. Concurrent Cancellation Race Condition (RefundLog Duplication)
* **Location:** `app/routers/bookings.py`
* **Bug:** Multiple concurrent cancellation requests for the same booking could pass the status verification concurrently, yielding multiple `200 OK` responses and creating duplicate `RefundLog` records.
* **Fix:** Synchronized the `cancel_booking` endpoint using the global `_booking_lock` and isolated database transactions within a dedicated `SessionLocal()` block to guarantee atomic checks and writes.
