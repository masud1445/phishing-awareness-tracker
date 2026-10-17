import os
import smtplib
from pathlib import Path

import pytest

import app as app_module
import database
import studio
import template_store
import sender_accounts
import sender_views
from cryptography.fernet import Fernet
from werkzeug.security import generate_password_hash
from sqlite_adapter import SQLiteConnection

TEST_OPERATOR_PASSWORD = "isolated-operator-test-password"
TEST_OPERATOR_HASH = generate_password_hash(TEST_OPERATOR_PASSWORD)


@pytest.fixture(autouse=True)
def block_real_smtp(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Real SMTP connections are forbidden in automated tests")
    monkeypatch.setattr(smtplib.SMTP, "connect", blocked)
    monkeypatch.setattr(smtplib.SMTP_SSL, "connect", blocked)


@pytest.fixture(autouse=True)
def operator_configuration(monkeypatch):
    monkeypatch.setitem(app_module.app.config, "ADMIN_USERNAME", "test-operator")
    monkeypatch.setitem(app_module.app.config, "ADMIN_PASSWORD_HASH", TEST_OPERATOR_HASH)
    monkeypatch.setitem(app_module.app.config, "APP_ENV", "local")
    monkeypatch.setitem(app_module.app.config, "SECRET_KEY", "isolated-test-signing-key")
    monkeypatch.setitem(app_module.app.config, "SESSION_COOKIE_SECURE", False)
    app_module.app.extensions["operator_login_throttle"]["attempts"].clear()
    app_module.app.extensions["operator_sessions"]["active"].clear()


def login_operator(client):
    client.get("/operator/login")
    with client.session_transaction() as state:
        token = state["studio_csrf"]
    response = client.post("/operator/login", data={"csrf_token": token,"username":"test-operator","password":TEST_OPERATOR_PASSWORD})
    assert response.status_code == 302
    return client


def post(client, url, data=None, **kwargs):
    """Send a legitimate regression request; negative security tests use raw POST."""
    if isinstance(data, (bytes, str)):
        with client.session_transaction() as state:
            token = state["studio_csrf"]
        kwargs["headers"] = dict(kwargs.get("headers", {})) | {"X-CSRF-Token": token}
    else:
        with client.session_transaction() as state:
            token = state["studio_csrf"]
        data = {"csrf_token":token} | dict(data or {})
    return client.post(url, data=data, **kwargs)


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
    for module in (app_module, template_store, studio, sender_accounts):
        monkeypatch.setattr(module, 'get_db_connection', factory)
    monkeypatch.setenv('SENDER_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('SMTP_FROM_EMAIL', 'awareness@example.com')
    monkeypatch.setenv('SMTP_ALLOWED_FROM_EMAILS', 'training@example.com')
    monkeypatch.setenv('SMTP_HOST', 'smtp.example.com')
    monkeypatch.setenv('SMTP_USER', 'awareness@example.com')
    monkeypatch.setenv('SMTP_PASSWORD', 'test-only-not-a-real-secret')
    monkeypatch.setenv('BASE_URL', 'https://awareness.example.com')
    monkeypatch.setitem(app_module.app.config, 'TESTING', True)
    monkeypatch.setitem(app_module.app.config, 'SECRET_KEY', 'isolated-test-key')
    client = login_operator(app_module.app.test_client())
    yield client, factory
    if mysql:
        connection = factory()
        try:
            with connection.cursor() as cursor:
                for table in ('campaign_email_settings', 'template_assets', 'email_templates', 'tracking_events', 'recipients', 'campaigns', 'sender_preferences', 'sender_accounts'):
                    cursor.execute('DROP TABLE ' + table)
        finally:
            connection.close()


@pytest.fixture
def campaign(db_client):
    client, factory = db_client
    response = post(client, '/campaigns/new', data={'campaign_name': 'Quarterly awareness', 'purpose': 'Training',
                           'campaign_template': 'basic-link', 'start_date': '2026-10-12', 'authorized': 'on'})
    assert response.status_code == 302
    post(client, '/campaigns/1/recipients/new', data={'full_name': 'Alex <Morgan>', 'email': 'alex@example.com', 'department': 'Finance'})
    post(client, '/campaigns/1/recipients/1/approve')
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
