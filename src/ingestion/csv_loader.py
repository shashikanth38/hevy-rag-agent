# src/ingestion/csv_loader.py
import boto3
from pathlib import Path
from src.config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    S3_BUCKET,
    S3_REGION,
    S3_PREFIX_WORKOUTS,
    DATA_DIR,
)
from src.db.snowflake_client import get_conn

LOCAL_CSV  = DATA_DIR / "hevy_export.csv"
STAGE_PATH = f"@HEVY.BRONZE.HEVY_STAGE/{S3_PREFIX_WORKOUTS}"


def upload_to_s3(csv_path: Path) -> None:
    s3 = boto3.client(
        "s3",
        aws_access_key_id     = AWS_ACCESS_KEY_ID,
        aws_secret_access_key = AWS_SECRET_ACCESS_KEY,
        region_name           = S3_REGION,
    )
    s3_key = f"{S3_PREFIX_WORKOUTS}{csv_path.name}"
    print(f"Uploading {csv_path.name} → s3://{S3_BUCKET}/{s3_key}")
    s3.upload_file(str(csv_path), S3_BUCKET, s3_key)
    print("Upload complete.")


def copy_into_bronze(conn) -> None:
    cursor = conn.cursor()
    print("Running COPY INTO RAW_WORKOUTS...")
    cursor.execute(f"""
        COPY INTO HEVY.BRONZE.RAW_WORKOUTS (
            title, start_time, end_time, description,
            exercise_title, superset_id, exercise_notes,
            set_index, set_type, weight_kg, reps,
            distance_km, duration_seconds, rpe,
            _source
        )
        FROM (
            SELECT
                $1, $2, $3, $4,
                $5, $6, $7,
                TRY_CAST($8 AS INTEGER),
                $9, $10, $11,
                $12, $13, $14,
                'csv'
            FROM {STAGE_PATH}
        )
        FILE_FORMAT = (
            TYPE                         = 'CSV'
            FIELD_DELIMITER              = ','
            SKIP_HEADER                  = 1
            NULL_IF                      = ('', 'NULL', 'null')
            EMPTY_FIELD_AS_NULL          = TRUE
            FIELD_OPTIONALLY_ENCLOSED_BY = '"'
        )
        ON_ERROR = 'CONTINUE'
    """)
    result = cursor.fetchone()
    print(f"Rows loaded:  {result[3]}")
    print(f"Rows errored: {result[4]}")
    cursor.close()


def verify(conn) -> None:
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM HEVY.BRONZE.RAW_WORKOUTS")
    print(f"Total rows in RAW_WORKOUTS: {cursor.fetchone()[0]}")
    cursor.execute("""
        SELECT DISTINCT title
        FROM HEVY.BRONZE.RAW_WORKOUTS
        ORDER BY 1
    """)
    titles = [r[0] for r in cursor.fetchall()]
    print(f"Workout titles: {titles}")
    cursor.close()


def run(csv_path: Path | None = None) -> None:
    path = csv_path or LOCAL_CSV
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    upload_to_s3(path)
    conn = get_conn()
    try:
        copy_into_bronze(conn)
        verify(conn)
    finally:
        conn.close()
        print("Done.")


if __name__ == "__main__":
    run()