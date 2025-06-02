import psycopg2
from psycopg2.extras import RealDictCursor

def get_db():
    conn = psycopg2.connect(
        dbname="first",
        user="postgres",
        password="root",
        host="localhost",
        port="5432",
        cursor_factory=RealDictCursor
    )
    if conn:
        print("Database connected successfully")
    else:
        print("Unsuccessful connection!")
    return conn