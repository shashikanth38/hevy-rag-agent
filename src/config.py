# src/config.py
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"

# ── Hevy API ──────────────────────────────────────────────────
HEVY_API_KEY  = os.getenv("HEVY_API_KEY")
HEVY_BASE_URL = "https://api.hevyapp.com"

# ── OpenAI ────────────────────────────────────────────────────
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY")
EMBEDDING_MODEL  = "text-embedding-3-small"

# ── ChromaDB ──────────────────────────────────────────────────
CHROMA_PATH = str(DATA_DIR / "chroma_db")

# ── Snowflake ─────────────────────────────────────────────────
SNOWFLAKE_ACCOUNT   = os.getenv("SNOWFLAKE_ACCOUNT")
SNOWFLAKE_USER      = os.getenv("SNOWFLAKE_USER")
SNOWFLAKE_PASSWORD  = os.getenv("SNOWFLAKE_PASSWORD")
SNOWFLAKE_WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE")
SNOWFLAKE_DATABASE  = os.getenv("SNOWFLAKE_DATABASE")
SNOWFLAKE_SCHEMA    = os.getenv("SNOWFLAKE_SCHEMA")
SNOWFLAKE_ROLE      = "HEVY_ROLE"
SNOWFLAKE_PRIVATE_KEY_PATH = os.getenv("SNOWFLAKE_PRIVATE_KEY_PATH", str(BASE_DIR / "rsa_key.p8"))
# ── AWS / S3 ──────────────────────────────────────────────────
S3_BUCKET             = os.getenv("S3_BUCKET")
S3_REGION             = "ap-southeast-1"
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")

# ── S3 prefixes ───────────────────────────────────────────────
S3_PREFIX_WORKOUTS     = "workouts/historical/"
S3_PREFIX_MEASUREMENTS = "measurements/"
S3_PREFIX_PAPERS       = "papers/"