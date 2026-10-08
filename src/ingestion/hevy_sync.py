# src/ingestion/hevy_sync.py
import json
import time
import boto3
import httpx
from datetime import datetime, timezone, timedelta
from src.config import (
    HEVY_API_KEY,
    HEVY_BASE_URL,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    S3_BUCKET,
    S3_REGION,
)
from src.db.snowflake_client import get_conn

SYNC_META_TABLE = "HEVY.BRONZE.SYNC_META"

# A scheduled sync must never hang forever holding a task slot, and the Hevy
# API does rate-limit. httpx has NO timeout by default.
HTTP_TIMEOUT  = httpx.Timeout(30.0, connect=10.0)
MAX_ATTEMPTS  = 4


class SyncError(RuntimeError):
    """Raised when the sync cannot safely advance the watermark."""


def _get_with_retry(url: str, *, headers: dict, params: dict | None = None) -> httpx.Response:
    """GET with a timeout and exponential backoff on 429/5xx."""
    last_exc: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = httpx.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < MAX_ATTEMPTS:
                    wait = 2 ** attempt
                    print(f"  HTTP {resp.status_code} — retrying in {wait}s "
                          f"(attempt {attempt}/{MAX_ATTEMPTS})")
                    time.sleep(wait)
                    continue
            return resp
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_exc = e
            if attempt < MAX_ATTEMPTS:
                wait = 2 ** attempt
                print(f"  {type(e).__name__} — retrying in {wait}s "
                      f"(attempt {attempt}/{MAX_ATTEMPTS})")
                time.sleep(wait)
            else:
                raise SyncError(
                    f"GET {url} failed after {MAX_ATTEMPTS} attempts: {e}"
                ) from last_exc
    raise SyncError(f"GET {url} exhausted retries")


def get_last_sync(cursor) -> datetime | None:
    """
    Read the watermark. A missing row means a genuine first run; anything
    else is a real failure and must surface.

    A bare `except: pass` here made a Snowflake error indistinguishable from
    a first run, silently resyncing only the last 7 days and leaving an
    unbounded gap behind it.
    """
    cursor.execute(f"""
        SELECT value FROM {SYNC_META_TABLE}
        WHERE key = 'last_workout_sync'
    """)
    row = cursor.fetchone()
    if not row or row[0] is None:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError as e:
        raise SyncError(
            f"Watermark {row[0]!r} is not a valid ISO timestamp; "
            f"refusing to guess a sync window."
        ) from e


def set_last_sync(cursor, dt: datetime) -> None:
    cursor.execute(f"""
        MERGE INTO {SYNC_META_TABLE} AS target
        USING (SELECT 'last_workout_sync' AS key, %s AS value) AS source
        ON target.key = source.key
        WHEN MATCHED THEN UPDATE SET value = source.value
        WHEN NOT MATCHED THEN INSERT (key, value)
            VALUES (source.key, source.value)
    """, (dt.isoformat(),))


def fetch_workout_events(since: datetime) -> list[dict]:
    headers   = {"api-key": HEVY_API_KEY}
    events    = []
    page      = 1
    since_str = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"Fetching workout events since {since_str}...")

    while True:
        url  = (
            f"{HEVY_BASE_URL}/v1/workouts/events"
            f"?since={since_str}&page={page}&pageSize=10"
        )
        resp = _get_with_retry(url, headers=headers)

        if resp.status_code == 400:
            print(f"Events 400 body: {resp.text}")
            return []
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        data  = resp.json()
        batch = data.get("events", [])
        if not batch:
            break
        events.extend(batch)
        print(f"  Page {page} — {len(batch)} events")
        if len(batch) < 10:
            break
        page += 1
        time.sleep(0.2)

    print(f"Total events fetched: {len(events)}")
    return events


def fetch_workouts_since(since: datetime) -> list[dict]:
    headers  = {"api-key": HEVY_API_KEY}
    workouts = []
    page     = 1

    print(f"Fallback: fetching workouts updated since {since.isoformat()}...")

    while True:
        resp = _get_with_retry(
            f"{HEVY_BASE_URL}/v1/workouts",
            headers = headers,
            params  = {"page": page, "pageSize": 10},
        )
        if resp.status_code == 400:
            print(f"Workouts 400 body: {resp.text}")
            break
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        data  = resp.json()
        batch = data.get("workouts", [])
        if not batch:
            break

        new_batch = []
        stop      = False
        for w in batch:
            updated_at = datetime.fromisoformat(
                w["updated_at"].replace("Z", "+00:00")
            )
            if updated_at > since:
                new_batch.append(w)
            else:
                stop = True
                break

        workouts.extend(new_batch)
        print(f"  Page {page} — {len(batch)} total, {len(new_batch)} new")

        if stop or len(batch) < 10:
            break
        page += 1
        time.sleep(0.2)

    print(f"Total new workouts fetched: {len(workouts)}")
    return workouts


def flatten_workout_to_rows(workout: dict) -> list[dict]:
    rows        = []
    workout_id  = workout.get("id", "")
    title       = workout.get("title", "")
    start_time  = workout.get("start_time", "")
    end_time    = workout.get("end_time", "")
    description = workout.get("description", "")

    for exercise in workout.get("exercises", []):
        exercise_title = exercise.get("title", "")
        superset_id    = exercise.get("superset_id") or ""
        exercise_notes = exercise.get("notes", "")

        for s in exercise.get("sets", []):
            distance_m  = s.get("distance_meters")
            distance_km = round(distance_m / 1000, 4) if distance_m else ""

            rows.append({
                "workout_id":       workout_id,
                "title":            title,
                "start_time":       start_time,
                "end_time":         end_time,
                "description":      description,
                "exercise_title":   exercise_title,
                "superset_id":      superset_id,
                "exercise_notes":   exercise_notes,
                "set_index":        s.get("index", 0),
                "set_type":         s.get("type", "normal"),
                "weight_kg":        s.get("weight_kg") if s.get("weight_kg") is not None else "",
                "reps":             s.get("reps") if s.get("reps") is not None else "",
                "distance_km":      distance_km,
                "duration_seconds": s.get("duration_seconds") or "",
                "rpe":              s.get("rpe") or "",
                "_source":          "api",
            })
    return rows


def upload_to_s3(rows: list[dict], run_ts: str) -> str:
    s3 = boto3.client(
        "s3",
        aws_access_key_id     = AWS_ACCESS_KEY_ID,
        aws_secret_access_key = AWS_SECRET_ACCESS_KEY,
        region_name           = S3_REGION,
    )
    s3_key  = f"workouts/incremental/{run_ts}/workouts.json"
    content = "\n".join(json.dumps(r) for r in rows)

    s3.put_object(
        Bucket      = S3_BUCKET,
        Key         = s3_key,
        Body        = content.encode("utf-8"),
        ContentType = "application/json",
    )
    print(f"Uploaded {len(rows)} rows → s3://{S3_BUCKET}/{s3_key}")
    return s3_key


def ensure_sync_meta_table(cursor) -> None:
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {SYNC_META_TABLE} (
            key   VARCHAR PRIMARY KEY,
            value VARCHAR
        )
    """)


def copy_into_bronze(cursor, s3_key: str) -> tuple[int, int, str]:
    cursor.execute(f"""
        COPY INTO HEVY.BRONZE.RAW_WORKOUTS (
            workout_id, title, start_time, end_time, description,
            exercise_title, superset_id, exercise_notes,
            set_index, set_type, weight_kg, reps,
            distance_km, duration_seconds, rpe, _source
        )
        FROM (
            SELECT
                $1:workout_id::VARCHAR,
                $1:title::VARCHAR,
                $1:start_time::VARCHAR,
                $1:end_time::VARCHAR,
                $1:description::VARCHAR,
                $1:exercise_title::VARCHAR,
                $1:superset_id::VARCHAR,
                $1:exercise_notes::VARCHAR,
                $1:set_index::INTEGER,
                $1:set_type::VARCHAR,
                $1:weight_kg::VARCHAR,
                $1:reps::VARCHAR,
                $1:distance_km::VARCHAR,
                $1:duration_seconds::VARCHAR,
                $1:rpe::VARCHAR,
                $1:_source::VARCHAR
            FROM @HEVY.BRONZE.HEVY_STAGE/{s3_key}
        )
        FILE_FORMAT = (TYPE = 'JSON')
        ON_ERROR    = 'CONTINUE'
    """)
    # Read COPY result columns BY NAME. Positionally, index 4 is error_limit
    # and index 5 is errors_seen — and with ON_ERROR='CONTINUE' error_limit
    # equals the row count, so result[4] looks exactly like "everything
    # failed" on a perfectly clean load.
    cols   = [d[0].lower() for d in cursor.description]
    row    = cursor.fetchone()
    result = dict(zip(cols, row))

    rows_loaded  = int(result.get("rows_loaded")  or 0)
    rows_parsed  = int(result.get("rows_parsed")  or 0)
    rows_errored = int(result.get("errors_seen")  or 0)
    status       = str(result.get("status") or "")
    first_error  = result.get("first_error")

    print(f"COPY status:              {status}")
    print(f"Rows parsed:              {rows_parsed}")
    print(f"Rows loaded into bronze:  {rows_loaded}")
    print(f"Rows errored:             {rows_errored}")
    if first_error:
        print(f"First error:              {first_error}")
    return rows_loaded, rows_errored, status


def run() -> None:
    """
    Incremental sync. The watermark advances ONLY after a clean load.

    Previously it advanced unconditionally. Combined with
    ON_ERROR = 'CONTINUE', a partially failed COPY moved the watermark past
    data that never arrived — permanent, silent loss, because nothing would
    ever look at that window again. Now a non-zero error count raises and
    the watermark stays put, so the run is simply repeatable.
    """
    # Capture the cutoff BEFORE fetching. Using "now" after the load would
    # skip any workout logged while the sync was running.
    sync_started_at = datetime.now(timezone.utc)

    conn   = get_conn()
    cursor = conn.cursor()

    try:
        ensure_sync_meta_table(cursor)
        conn.commit()

        last_sync = get_last_sync(cursor)
        if not last_sync:
            print("No previous sync found — defaulting to 7 days ago.")
            last_sync = sync_started_at - timedelta(days=7)

        print(f"Watermark: {last_sync.isoformat()}")
        print(f"Window:    {last_sync.isoformat()} → {sync_started_at.isoformat()}")

        events = fetch_workout_events(since=last_sync)

        if events:
            updated = [
                e["workout"]
                for e in events
                if e.get("type") == "updated" and "workout" in e
            ]
            skipped = len(events) - len(updated)
            if skipped:
                print(f"Note: {skipped} event(s) were not type='updated' "
                      f"(deletions are not yet propagated downstream).")
        else:
            print("Events endpoint empty — using workouts endpoint fallback...")
            updated = fetch_workouts_since(since=last_sync)

        print(f"Workouts to upsert: {len(updated)}")

        if not updated:
            # Nothing to load, so nothing can have failed: safe to advance.
            set_last_sync(cursor, sync_started_at)
            conn.commit()
            print("No new workouts. Watermark advanced.")
            return

        all_rows = []
        for w in updated:
            all_rows.extend(flatten_workout_to_rows(w))
        print(f"Total set rows to load: {len(all_rows)}")

        if not all_rows:
            raise SyncError(
                f"{len(updated)} workout(s) fetched but they flattened to 0 set "
                f"rows. Refusing to advance the watermark — this usually means "
                f"the API response shape changed."
            )

        run_ts = sync_started_at.strftime("%Y%m%d_%H%M%S")
        s3_key = upload_to_s3(all_rows, run_ts)

        rows_loaded, rows_errored, status = copy_into_bronze(cursor, s3_key)

        if rows_errored > 0 or status.lower() not in ("loaded", "load_succeeded"):
            # NOTE: the connector runs with autocommit, so the COPY has
            # already committed — whatever loaded is in bronze and cannot be
            # rolled back here. The protection is that the watermark stays
            # put, making the window repeatable. Silver collapses exact
            # duplicate rows, so a re-run is safe.
            raise SyncError(
                f"COPY INTO status={status!r} with {rows_errored} errored "
                f"row(s) ({rows_loaded} loaded). The watermark was NOT "
                f"advanced, so this window will be retried on the next run.\n"
                f"Rows that did load are already in bronze (autocommit).\n"
                f"Staged file: s3://{S3_BUCKET}/{s3_key}\n"
                f"Inspect with:\n"
                f"  SELECT file_name, status, row_count, error_count, "
                f"first_error_message\n"
                f"  FROM TABLE(INFORMATION_SCHEMA.COPY_HISTORY("
                f"TABLE_NAME=>'HEVY.BRONZE.RAW_WORKOUTS',\n"
                f"       START_TIME=>DATEADD('hour',-3,CURRENT_TIMESTAMP())))\n"
                f"  ORDER BY last_load_time DESC;"
            )

        if rows_loaded == 0:
            raise SyncError(
                f"COPY INTO loaded 0 rows from {len(all_rows)} staged rows with "
                f"no errors reported. Refusing to advance the watermark. The "
                f"file may already have been loaded (COPY load history) — "
                f"check s3://{S3_BUCKET}/{s3_key}"
            )

        # Only now is it safe to move the watermark forward.
        set_last_sync(cursor, sync_started_at)
        conn.commit()
        print(f"Watermark advanced to {sync_started_at.isoformat()}")
        print("Sync complete.")

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    run()