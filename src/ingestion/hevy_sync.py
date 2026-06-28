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


def get_last_sync(cursor) -> datetime | None:
    try:
        cursor.execute(f"""
            SELECT value FROM {SYNC_META_TABLE}
            WHERE key = 'last_workout_sync'
        """)
        row = cursor.fetchone()
        if row:
            return datetime.fromisoformat(row[0])
    except Exception:
        pass
    return None


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
        resp = httpx.get(url, headers=headers)

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
        resp = httpx.get(
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


def copy_into_bronze(cursor, s3_key: str) -> None:
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
    result = cursor.fetchone()
    print(f"Rows loaded into bronze:  {result[3]}")
    print(f"Rows errored:             {result[4]}")


def run() -> None:
    conn   = get_conn()
    cursor = conn.cursor()

    ensure_sync_meta_table(cursor)
    conn.commit()

    last_sync = get_last_sync(cursor)
    if not last_sync:
        print("No previous sync found — defaulting to 7 days ago.")
        last_sync = datetime.now(timezone.utc) - timedelta(days=7)

    events = fetch_workout_events(since=last_sync)

    if events:
        updated = [
            e["workout"]
            for e in events
            if e.get("type") == "updated" and "workout" in e
        ]
    else:
        print("Events endpoint empty — using workouts endpoint fallback...")
        updated = fetch_workouts_since(since=last_sync)

    print(f"Workouts to upsert: {len(updated)}")

    if updated:
        all_rows = []
        for w in updated:
            all_rows.extend(flatten_workout_to_rows(w))
        print(f"Total set rows to load: {len(all_rows)}")

        now    = datetime.now(timezone.utc)
        run_ts = now.strftime("%Y%m%d_%H%M%S")
        s3_key = upload_to_s3(all_rows, run_ts)
        copy_into_bronze(cursor, s3_key)

    set_last_sync(cursor, datetime.now(timezone.utc))
    conn.commit()
    cursor.close()
    conn.close()
    print("Sync complete.")


if __name__ == "__main__":
    run()