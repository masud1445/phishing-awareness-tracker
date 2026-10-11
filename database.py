"""MySQL connections and non-destructive schema initialization."""
import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    return pymysql.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'),
        port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD', ''),
        database=os.getenv('DB_NAME', 'phishing_awareness_tracker'),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def initialize_database():
    """Add missing tables only; never reset existing campaigns or recipients."""
    connection = get_db_connection()
    try:
        schema = Path(__file__).with_name('schema').joinpath('mysql.sql').read_text()
        with connection.cursor() as cursor:
            for statement in schema.split(';'):
                if statement.strip():
                    cursor.execute(statement)
    finally:
        connection.close()
