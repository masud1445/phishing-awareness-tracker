"""Test-only relational adapter; the application always uses MySQL."""
import sqlite3

class SQLiteCursor:
    def __init__(self, connection):
        self.cursor = connection.cursor()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cursor.close()

    def execute(self, query, params=None):
        self.cursor.execute(query.replace('%s', '?').replace(' FOR UPDATE', ''), params or ())
        return self.cursor.rowcount

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]


class SQLiteConnection:
    def __init__(self, path):
        self.connection = sqlite3.connect(path, isolation_level=None, timeout=20)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys = ON')

    def cursor(self):
        return SQLiteCursor(self.connection)

    def begin(self):
        self.connection.execute('BEGIN IMMEDIATE')

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()
