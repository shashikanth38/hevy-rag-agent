# src/ingestion/measurements_loader.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import time
import httpx
from src.config import HEVY_API_KEY, HEVY_BASE_URL
from src.db.snowflake_client import get_conn


def fetch_all_measurements() -> list[dict]:
    headers      = {"api-key": HEVY_API_KEY}
    measurements = []
    page         = 1

    resp       = httpx.get(
        f"{HEVY_BASE_URL}/v1/body_measurements",
        headers = headers,
        params  = {"page": 1, "pageSize": 10},
    )
    resp.raise_for_status()
    data       = resp.json()
    page_count = data.get("page_count", 1)
    measurements.extend(data.get("body_measurements", []))
    print(f"  Page 1 — {len(data.get('body_measurements', []))} measurements (total pages: {page_count})")

    for page in range(2, page_count + 1):
        resp = httpx.get(
            f"{HEVY_BASE_URL}/v1/body_measurements",
            headers = headers,
            params  = {"page": page, "pageSize": 10},
        )
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        batch = resp.json().get("body_measurements", [])
        if not batch:
            break
        measurements.extend(batch)
        print(f"  Page {page} — {len(batch)} measurements")
        time.sleep(0.2)

    print(f"Total measurements fetched: {len(measurements)}")
    return measurements


def load_into_snowflake(measurements: list[dict]) -> None:
    if not measurements:
        print("No measurements to load.")
        return

    conn   = get_conn()
    cursor = conn.cursor()
    cursor.execute("TRUNCATE TABLE HEVY.BRONZE.RAW_MEASUREMENTS")  # ← add this
    # print first item so we can see the API structure
    print(f"Sample measurement: {measurements[0]}")

    rows = []
    for m in measurements:
        rows.append((
            m.get("date"),
            m.get("weight_kg"),
            m.get("body_fat_percentage"),
        ))

    cursor.executemany("""
        INSERT INTO HEVY.BRONZE.RAW_MEASUREMENTS (
            measurement_date,
            weight_kg,
            body_fat_pct
        ) VALUES (%s, %s, %s)
    """, rows)

    conn.commit()
    cursor.close()
    conn.close()
    print(f"Loaded {len(rows)} measurements into RAW_MEASUREMENTS")


def run() -> None:
    measurements = fetch_all_measurements()
    load_into_snowflake(measurements)


if __name__ == "__main__":
    run()