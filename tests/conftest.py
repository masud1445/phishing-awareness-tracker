import os
from pathlib import Path

import pytest

import app as app_module
import database
import studio
import template_store
from sqlite_adapter import SQLiteConnection


@pytest.fixture
def db_client(tmp_path, monkeypatch):
    """Isolated relational integration DB; never expose SQLite as an app backend.

    Set TEST_MYSQL_DATABASE to an EXISTING, empty test-only database to run the
    same integration tests on MySQL. The fixture refuses populated databases.
    """
    if os.getenv('TEST_MYSQL_DATABASE'):
        if os.environ['TEST_MYSQL_DATABASE'] == os.getenv('DB_NAME', 'phishing_awareness_tracker'):
            pytest.fail('Refusing to use the application database as TEST_MYSQL_DATABASE.')
        monkeypatch.setenv('DB_NAME', os.environ['TEST_MYSQL_DATABASE'])
        factory = database.get_db_connection
        connection = factory()
        with connection.cursor() as cursor:
            cursor.execute('SHOW TABLES')
            if cursor.fetchall():
                connection.close()
                pytest.fail('TEST_MYSQL_DATABASE must be empty: refusing to erase existing tables.')
        connection.close()
        database.initialize_database()
        mysql = True
    else:
        path = tmp_path / 'isolated.sqlite3'
        factory = lambda: SQLiteConnection(path)
        connection = factory()
        connection.connection.executescript(Path(__file__).with_name('sqlite_schema.sql').read_text())
        connection.close()
        mysql = False
    for module in (app_module, template_store, studio):
        monkeypatch.setattr(module, 'get_db_connection', factory)
    monkeypatch.setenv('SMTP_FROM_EMAIL', 'awareness@example.com')
    monkeypatch.setenv('SMTP_ALLOWED_FROM_EMAILS', 'training@example.com')
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_USER', 'awareness@example.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'test-only-not-a-real-secret')
    monkeypatch.setenv('BASE_URL', 'https://awareness.example.com')
    monkeypatch.setitem(app_module.app.config, 'TESTING', True)
    monkeypatch.setitem(app_module.app.config, 'SECRET_KEY', 'isolated-test-key')
    client = app_module.app.test_client()
    yield client, factory
    if mysql:
        connection = factory()
        try:
            with connection.cursor() as cursor:
                for table in ('campaign_email_settings', 'template_assets', 'email_templates', 'tracking_events', 'recipients', 'campaigns'):
                    cursor.execute('DROP TABLE ' + table)
        finally:
            connection.close()


@pytest.fixture
def campaign(db_client):
    client, factory = db_client
    response = client.post('/campaigns/new', data={'campaign_name': 'Quarterly awareness', 'purpose': 'Training',
                           'campaign_template': 'basic-link', 'start_date': '2026-10-12', 'authorized': 'on'})
    assert response.status_code == 302
    client.post('/campaigns/1/recipients/new', data={'full_name': 'Alex <Morgan>', 'email': 'alex@example.com', 'department': 'Finance'})
    client.post('/campaigns/1/recipients/1/approve')
    return client, factory


def csrf(client):
    client.get('/templates/new')
    with client.session_transaction() as session:
        return session['studio_csrf']


def template_form():
    return {'name': 'Quarterly reminder', 'subject': 'Review {{campaign_name}}',
            'body': 'Hello {{recipient_name}},\nReview {{tracking_url}}',
            'sender_name': 'Awareness Team', 'sender_email': 'awareness@example.com',
            'reference_link': 'https://example.com/awareness'}
