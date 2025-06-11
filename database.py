import os
import psycopg2
from psycopg2.extras import RealDictCursor

def get_db():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise Exception("DATABASE_URL environment variable not set")
    conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    return conn
