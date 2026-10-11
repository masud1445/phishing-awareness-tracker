import pytest

import email_service


def campaign():
    return {'id': 1, 'campaign_name': 'Awareness', 'authorized': 1, 'status': 'active'}


def recipient():
    return {'id': 1, 'campaign_id': 1, 'full_name': 'Alex', 'email': 'alex@example.com', 'approved': 1, 'tracking_token': 'abc'}


@pytest.mark.parametrize('kind', ['inactive', 'unapproved', 'wrong-campaign', 'unauthorized', 'missing-authorization'])
def test_delivery_service_guards_precede_smtp(kind, monkeypatch):
    c, r = campaign(), recipient()
    if kind == 'inactive': c['status'] = 'draft'
    if kind == 'unapproved': r['approved'] = 0
    if kind == 'wrong-campaign': r['campaign_id'] = 2
    if kind == 'unauthorized': c['authorized'] = 0
    if kind == 'missing-authorization': c.pop('authorized')
    called = []
    monkeypatch.setattr(email_service.smtplib, 'SMTP', lambda *args, **kwargs: called.append(True))
    with pytest.raises(email_service.EmailDeliveryError):
        email_service.send_campaign_email(c, r)
    assert not called


def test_recipient_and_sender_header_injection_blocked(monkeypatch):
    monkeypatch.setenv('SMTP_FROM_EMAIL', 'awareness@example.com')
    r = recipient() | {'email': 'alex@example.com\nBcc: victim@example.com'}
    with pytest.raises(email_service.EmailDeliveryError):
        email_service.build_campaign_message(campaign(), r)
    monkeypatch.setenv('SMTP_FROM_NAME', 'Team\nInjected')
    with pytest.raises(email_service.EmailDeliveryError):
        email_service.build_campaign_message(campaign(), recipient())


@pytest.mark.parametrize('token', ['../outside', 'abc?password=test', '', 'abc\nInjected'])
def test_tracking_token_cannot_inject_url(token):
    with pytest.raises(email_service.EmailDeliveryError):
        email_service.build_tracking_url(token)
