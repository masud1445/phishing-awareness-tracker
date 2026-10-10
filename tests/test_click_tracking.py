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


def make_recipient():
    return {
        "recipient_id": 1,
        "campaign_id": 1,
        "full_name": "Authorized Test User",
        "status": "sent",
        "campaign_name": "Test Campaign",
    }


def test_invalid_tracking_token_returns_404(monkeypatch):
    connection = FakeConnection([None])

    monkeypatch.setattr(
        app_module,
        "get_db_connection",
        lambda: connection,
    )

    client = app_module.app.test_client()

    response = client.get("/t/INVALID-TOKEN")

    assert response.status_code == 404
    assert b"invalid or expired campaign link" in response.data.lower()
    assert connection.closed is True


def test_first_click_is_recorded(monkeypatch):
    connection = FakeConnection([
        make_recipient(),
        None,
    ])

    monkeypatch.setattr(
        app_module,
        "get_db_connection",
        lambda: connection,
    )

    monkeypatch.setattr(
        app_module,
        "render_template",
        lambda *args, **kwargs: "Awareness landing page",
    )

    client = app_module.app.test_client()

    response = client.get("/t/TEST-TOKEN")

    assert response.status_code == 200

    queries = [
        query
        for query, params in connection.cursor_instance.executed
    ]

    insert_queries = [
        query for query in queries
        if "INSERT INTO tracking_events" in query
    ]

    update_queries = [
        query for query in queries
        if "UPDATE recipients" in query
    ]

    assert len(insert_queries) == 1
    assert len(update_queries) == 1
    assert "SET status = 'clicked'" in update_queries[0]
    assert connection.closed is True


def test_duplicate_click_is_not_inserted_again(monkeypatch):
    connection = FakeConnection([
        make_recipient(),
        {"id": 99},
    ])

    monkeypatch.setattr(
        app_module,
        "get_db_connection",
        lambda: connection,
    )

    monkeypatch.setattr(
        app_module,
        "render_template",
        lambda *args, **kwargs: "Awareness landing page",
    )

    client = app_module.app.test_client()

    response = client.get("/t/TEST-TOKEN")

    assert response.status_code == 200

    queries = [
        query
        for query, params in connection.cursor_instance.executed
    ]

    insert_queries = [
        query for query in queries
        if "INSERT INTO tracking_events" in query
    ]

    update_queries = [
        query for query in queries
        if "UPDATE recipients" in query
    ]

    assert insert_queries == []
    assert len(update_queries) == 1
    assert connection.closed is True
