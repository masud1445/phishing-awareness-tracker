from conftest import post, login_operator
import app as app_module


class FakeCursor:
    def __init__(self, fetch_results):
        self.fetch_results = list(fetch_results)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchone(self):
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return None


class FakeConnection:
    def __init__(self, fetch_results):
        self.cursor_instance = FakeCursor(fetch_results)
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


def make_campaign():
    return {
        "id": 1,
        "campaign_name": "Test Campaign",
        "status": "active",
    }


def make_recipient():
    return {
        "id": 1,
        "campaign_id": 1,
        "full_name": "Authorized Test User",
        "email": "test@example.com",
        "approved": 1,
        "tracking_token": "TEST-TOKEN",
        "status": "pending",
        "sent_at": None,
    }


def test_unapproved_recipient_is_blocked(monkeypatch):
    connection = FakeConnection([
        make_campaign(),
        None,
    ])

    monkeypatch.setattr(
        app_module,
        "get_db_connection",
        lambda: connection,
    )

    sent = {"called": False}

    def fake_send(*args, **kwargs):
        sent["called"] = True

    monkeypatch.setattr(
        app_module,
        "send_campaign_email",
        fake_send,
    )

    client = login_operator(app_module.app.test_client())

    response = post(client,
        "/campaigns/1/recipients/1/send-email"
    )

    assert response.status_code == 403
    assert b"not approved" in response.data.lower()
    assert sent["called"] is False
    assert connection.closed is True


def test_approved_recipient_email_flow(monkeypatch):
    connection = FakeConnection([
        make_campaign(),
        make_recipient(),
    ])

    monkeypatch.setattr(
        app_module,
        "get_db_connection",
        lambda: connection,
    )

    sent = {"called": False}

    def fake_send(campaign, recipient):
        sent["called"] = True

    monkeypatch.setattr(
        app_module,
        "send_campaign_email",
        fake_send,
    )

    client = login_operator(app_module.app.test_client())

    response = post(client,
        "/campaigns/1/recipients/1/send-email"
    )

    assert response.status_code == 302
    assert sent["called"] is True
    assert connection.closed is True

    update_queries = [
        query
        for query, params in connection.cursor_instance.executed
        if "UPDATE recipients" in query
    ]

    assert len(update_queries) == 1
    assert "COALESCE(sent_at, CURRENT_TIMESTAMP)" in update_queries[0]
    assert "WHEN status = 'clicked' THEN status" in update_queries[0]


def test_email_delivery_failure_does_not_mark_sent(monkeypatch):
    connection = FakeConnection([
        make_campaign(),
        make_recipient(),
    ])

    monkeypatch.setattr(
        app_module,
        "get_db_connection",
        lambda: connection,
    )

    def fake_send(campaign, recipient):
        raise app_module.EmailDeliveryError("Test SMTP failure")

    monkeypatch.setattr(
        app_module,
        "send_campaign_email",
        fake_send,
    )

    client = login_operator(app_module.app.test_client())

    response = post(client,
        "/campaigns/1/recipients/1/send-email"
    )

    assert response.status_code == 400
    assert b"email delivery blocked" in response.data.lower()

    update_queries = [
        query
        for query, params in connection.cursor_instance.executed
        if "UPDATE recipients" in query
    ]

    assert update_queries == []
    assert connection.closed is True
