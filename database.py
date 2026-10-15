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
            cursor.execute("SELECT COUNT(*) AS total FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='email_templates' AND column_name='sender_account_id'")
            if not cursor.fetchone()['total']:
                cursor.execute('ALTER TABLE email_templates ADD COLUMN sender_account_id INTEGER NULL')
            cursor.execute("SELECT COUNT(*) AS total FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='email_templates' AND column_name='presentation'")
            if not cursor.fetchone()['total']:
                cursor.execute('ALTER TABLE email_templates ADD COLUMN presentation TEXT NULL')
            cursor.execute('SELECT id FROM sender_preferences WHERE id=1')
            if not cursor.fetchone():
                cursor.execute('INSERT INTO sender_preferences (id,default_sender_id) VALUES (1,NULL)')
    finally:
        connection.close()
