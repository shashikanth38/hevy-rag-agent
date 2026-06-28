# src/ingestion/exercise_template_loader.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import time
import httpx
import json
from src.config import HEVY_API_KEY, HEVY_BASE_URL
from src.db.snowflake_client import get_conn


def fetch_all_templates() -> list[dict]:
    headers   = {"api-key": HEVY_API_KEY}
    templates = []
    page      = 1

    # get total page count first
    resp = httpx.get(
        f"{HEVY_BASE_URL}/v1/exercise_templates",
        headers = headers,
        params  = {"page": 1, "pageSize": 10},
    )
    resp.raise_for_status()
    data       = resp.json()
    page_count = data.get("page_count", 1)
    templates.extend(data.get("exercise_templates", []))
    print(f"  Page 1 — {len(data.get('exercise_templates', []))} templates (total pages: {page_count})")

    for page in range(2, page_count + 1):
        resp = httpx.get(
            f"{HEVY_BASE_URL}/v1/exercise_templates",
            headers = headers,
            params  = {"page": page, "pageSize": 10},
        )
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        batch = resp.json().get("exercise_templates", [])
        if not batch:
            break
        templates.extend(batch)
        print(f"  Page {page} — {len(batch)} templates")
        time.sleep(0.2)

    print(f"Total templates fetched: {len(templates)}")
    return templates


def load_into_snowflake(templates: list[dict]) -> None:
    conn   = get_conn()
    cursor = conn.cursor()

    cursor.execute("TRUNCATE TABLE HEVY.BRONZE.DIM_EXERCISE_TEMPLATES")

    rows = []
    for t in templates:
        secondary = t.get("secondary_muscle_groups", [])
        rows.append((
            t.get("id"),
            t.get("title"),
            t.get("equipment"),               # correct field name
            t.get("primary_muscle_group"),    # correct field name
            json.dumps(secondary),
            t.get("is_custom", False),
        ))

    cursor.executemany("""
        INSERT INTO HEVY.BRONZE.DIM_EXERCISE_TEMPLATES (
            exercise_template_id,
            title,
            equipment_category,
            primary_muscle_group,
            secondary_muscle_groups,
            is_custom
        ) VALUES (%s, %s, %s, %s, %s, %s)
    """, rows)

    conn.commit()
    cursor.close()
    conn.close()
    print(f"Loaded {len(rows)} templates into DIM_EXERCISE_TEMPLATES")


def run() -> None:
    templates = fetch_all_templates()
    load_into_snowflake(templates)


if __name__ == "__main__":
    run()