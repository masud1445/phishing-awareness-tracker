import io
import smtplib

import pytest

import app as app_module
import email_service
import template_store
from conftest import csrf, template_form
from tests_helper import read_one
from test_uploads import image_bytes


def save(client, **overrides):
    values = template_form() | {'csrf_token': csrf(client)} | overrides
    return client.post('/templates/new', data=values, content_type='multipart/form-data')


def apply(client, template_id=1):
    return client.post('/campaigns/1/studio', data={'csrf_token': csrf(client), 'template_id': str(template_id), 'authorized': 'on'})


def test_library_create_edit_and_sample_preview(db_client):
    client, factory = db_client
    assert client.get('/templates').status_code == 200
    assert save(client).status_code == 302
    assert b'Quarterly reminder' in client.get('/templates').data
    values = template_form() | {'csrf_token': csrf(client), 'subject': 'Updated subject'}
    assert client.post('/templates/1/edit', data=values).status_code == 302
    assert template_store.get_template(1)['subject'] == 'Updated subject'
    response = client.post('/templates/1/edit', data=values | {'action': 'preview'})
    assert response.status_code == 200
    assert b'Sample preview' in response.data
    assert b'PREVIEW-ONLY' in response.data
    assert client.get('/templates/999/edit').status_code == 404


def test_template_validation_and_csrf(db_client):
    client, factory = db_client
    assert client.post('/templates/new', data=template_form()).status_code == 400
    response = save(client, sender_email='spoof@example.net')
    assert b'authenticated SMTP identity' in response.data
    assert template_store.list_templates() == []
    response = save(client, body='<form><input type="password"></form>')
    assert b'plain text' in response.data.lower()


def test_no_smtp_secrets_in_ui(db_client):
    client, factory = db_client
    response = client.get('/templates/new')
    assert b'SMTP_PASSWORD' not in response.data
    assert b'test-only-not-a-real-secret' not in response.data
    assert b'training@example.com' in response.data


def test_assets_saved_outside_static_and_safe_download(db_client):
    client, factory = db_client
    response = save(client, image=(io.BytesIO(image_bytes()), 'illustration.png'),
                    attachments=[(io.BytesIO(b'guide'), 'Awareness guide.txt')])
    assert response.status_code == 302
    template = template_store.get_template(1)
    assert len(template['assets']) == 2
    assert template['assets'][1]['filename'] == 'Awareness_guide.txt'
    response = client.get('/template-assets/2')
    assert response.data == b'guide'
    assert response.headers['X-Content-Type-Options'] == 'nosniff'
    assert 'attachment' in response.headers['Content-Disposition']
    assert 'sandbox' in response.headers['Content-Security-Policy']
    assert client.get('/template-assets/1').mimetype == 'image/png'
    assert client.get('/template-assets/999').status_code == 404
    assert client.get('/template-assets/../../etc/passwd').status_code == 404


def test_invalid_upload_is_atomic(db_client):
    client, factory = db_client
    response = save(client, attachments=[(io.BytesIO(b'safe'), 'guide.txt'), (io.BytesIO(b'script'), 'script.exe')])
    assert b'Allowed files' in response.data
    assert template_store.list_templates() == []


def test_attachment_count_and_image_replacement(db_client):
    client, factory = db_client
    response = save(client, attachments=[(io.BytesIO(b'guide'), f'guide{i}.txt') for i in range(6)])
    assert b'at most five' in response.data
    assert template_store.list_templates() == []
    assert save(client, image=(io.BytesIO(image_bytes()), 'first.png')).status_code == 302
    values = template_form() | {'csrf_token': csrf(client), 'image': (io.BytesIO(image_bytes()), 'second.png')}
    assert b'at most one illustration' in client.post('/templates/1/edit', data=values).data
    values['image'] = (io.BytesIO(image_bytes()), 'second.png')
    values['remove_assets'] = '1'
    assert client.post('/templates/1/edit', data=values).status_code == 302
    assert [a['filename'] for a in template_store.get_template(1)['assets']] == ['second.png']


def test_campaign_snapshot_survives_library_edit_and_asset_removal(campaign):
    client, factory = campaign
    save(client, attachments=(io.BytesIO(b'original'), 'guide.txt'))
    assert apply(client).status_code == 302
    original = template_store.campaign_template(1)
    values = template_form() | {'csrf_token': csrf(client), 'subject': 'New subject', 'remove_assets': '1'}
    assert client.post('/templates/1/edit', data=values).status_code == 302
    assert template_store.campaign_template(1) == original
    assert template_store.get_template(1)['assets'] == []
    assert template_store.get_asset(original['assets'][0]['id'])['data'] == b'original'
    response = client.get('/campaigns/1/email-preview')
    assert b'Review Quarterly awareness' in response.data
    assert b'New subject' not in response.data


def test_apply_requires_authorization_and_valid_campaign(campaign):
    client, factory = campaign
    save(client)
    assert client.post('/campaigns/1/studio', data={'template_id': '1'}).status_code == 400
    response = client.post('/campaigns/1/studio', data={'csrf_token': csrf(client), 'template_id': '1'})
    assert b'Authorization confirmation' in response.data
    assert template_store.campaign_template(1) is None
    assert client.get('/campaigns/999/studio').status_code == 404
    response = client.post('/campaigns/1/studio', data={'csrf_token': csrf(client), 'authorized': 'on', 'template_id': '999'})
    assert b'Choose an existing template' in response.data


def test_send_requires_review_confirmation_and_active_campaign(campaign, monkeypatch):
    client, factory = campaign
    save(client)
    apply(client)
    sent = []
    monkeypatch.setattr(app_module, 'send_campaign_email', lambda *args: sent.append(args))
    data = {'csrf_token': csrf(client), 'authorized': 'on'}
    assert client.post('/campaigns/1/recipients/1/send-email', data=data).status_code == 400
    client.get('/campaigns/1/email-preview')
    assert client.post('/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client)}).status_code == 400
    # Real delivery service validates activity even when route has a reviewed email.
    monkeypatch.setattr(app_module, 'send_campaign_email', email_service.send_campaign_email)
    assert client.post('/campaigns/1/recipients/1/send-email', data=data).status_code == 400
    assert read_one(factory, 'SELECT sent_at FROM recipients WHERE id=1')['sent_at'] is None
    assert sent == []


class FakeSMTP:
    messages = []
    events = []
    def __init__(self, host, port, timeout):
        self.events.append(('connect', host, port, timeout))
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def starttls(self, context):
        assert context.check_hostname
        import ssl
        assert context.verify_mode == ssl.CERT_REQUIRED
        self.events.append(('tls',))
    def login(self, user, password):
        self.events.append(('login', user))
    def send_message(self, message):
        self.events.append(('send',))
        self.messages.append(message)


def test_preview_matches_mime_delivery_and_preserves_scoring(campaign, monkeypatch):
    client, factory = campaign
    save(client, image=(io.BytesIO(image_bytes()), 'image.png'), attachments=(io.BytesIO(b'awareness guide'), 'guide.txt'))
    apply(client)
    client.post('/campaigns/1/launch')
    client.get('/campaigns/1/email-preview')
    from template_content import render_email
    recipient = read_one(factory, 'SELECT * FROM recipients WHERE id=1')
    campaign_row = read_one(factory, 'SELECT * FROM campaigns WHERE id=1')
    template = template_store.campaign_template(1)
    expected = render_email(template, campaign_row, recipient, email_service.build_tracking_url(recipient['tracking_token']), 'cid:campaign-illustration')
    FakeSMTP.messages = []; FakeSMTP.events = []
    monkeypatch.setattr(email_service.smtplib, 'SMTP', FakeSMTP)
    response = client.post('/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client), 'authorized': 'on'})
    assert response.status_code == 302
    assert len(FakeSMTP.messages) == 1
    message = FakeSMTP.messages[0]
    assert message['Subject'] == expected['subject']
    assert message.get_body(preferencelist=('plain',)).get_content().rstrip() == expected['text']
    assert message.get_body(preferencelist=('html',)).get_content().rstrip() == expected['html']
    assert list(part.get_filename() for part in message.iter_attachments()) == ['guide.txt']
    assert any(part.get('Content-ID') == '<campaign-illustration>' for part in message.walk())
    assert [event[0] for event in FakeSMTP.events] == ['connect', 'tls', 'login', 'send']
    assert read_one(factory, 'SELECT status FROM recipients WHERE id=1')['status'] == 'sent'
    assert b'Template is locked' in apply(client).data
    token = recipient['tracking_token']
    assert client.get('/t/' + token).status_code == 200
    assert client.get('/t/' + token).status_code == 200
    assert read_one(factory, 'SELECT COUNT(*) AS total FROM tracking_events')['total'] == 1
    assert b'0.0%' in client.get('/campaigns/1/results').data
    assert client.get('/').status_code == 200
    # Resending does not erase clicked status or the original sent_at.
    first = read_one(factory, 'SELECT sent_at FROM recipients WHERE id=1')['sent_at']
    client.get('/campaigns/1/email-preview')
    assert client.post('/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client), 'authorized': 'on'}).status_code == 302
    row = read_one(factory, 'SELECT status, sent_at FROM recipients WHERE id=1')
    assert row['status'] == 'clicked' and row['sent_at'] == first


def test_changed_snapshot_invalidates_review(campaign, monkeypatch):
    client, factory = campaign
    save(client)
    apply(client)
    client.post('/campaigns/1/launch')
    client.get('/campaigns/1/email-preview')
    save(client, subject='Different content')
    apply(client, 2)
    sent = []
    monkeypatch.setattr(app_module, 'send_campaign_email', lambda *args: sent.append(args))
    assert client.post('/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client), 'authorized': 'on'}).status_code == 400
    assert sent == []


def test_preview_selects_only_approved_recipients(campaign):
    client, factory = campaign
    save(client); apply(client)
    client.post('/campaigns/1/recipients/new', data={'full_name': 'Unapproved User', 'email': 'other@example.com'})
    assert client.get('/campaigns/1/email-preview?recipient_id=2').status_code == 404
    assert client.post('/campaigns/1/recipients/2/send-email').status_code == 403
    response = client.get('/campaigns/1/email-preview')
    assert b'Unapproved User' not in response.data
    assert b'sandbox' in response.data
    assert read_one(factory, 'SELECT COUNT(*) AS total FROM tracking_events')['total'] == 0


def test_legacy_campaign_preview_and_delivery(campaign, monkeypatch):
    client, factory = campaign
    assert template_store.campaign_template(1) is None
    response = client.get('/campaigns/1/email-preview')
    assert response.status_code == 200
    assert b'Security Awareness: Review Required Information' in response.data
    client.post('/campaigns/1/launch')
    FakeSMTP.messages = []; FakeSMTP.events = []
    monkeypatch.setattr(email_service.smtplib, 'SMTP', FakeSMTP)
    assert client.post('/campaigns/1/recipients/1/send-email').status_code == 302
    assert FakeSMTP.messages[0]['Subject'] == 'Security Awareness: Review Required Information'
    assert b'No password, OTP' in client.get('/t/' + read_one(factory, 'SELECT tracking_token FROM recipients WHERE id=1')['tracking_token']).data
    assert client.get('/campaigns/1/results').status_code == 200
    assert client.get('/campaigns/1/recipients').status_code == 200


def test_smtp_failure_does_not_update_delivery(campaign, monkeypatch):
    client, factory = campaign
    client.post('/campaigns/1/launch')
    class BrokenSMTP(FakeSMTP):
        def login(self, *args):
            raise smtplib.SMTPAuthenticationError(535, b'provider detail')
    monkeypatch.setattr(email_service.smtplib, 'SMTP', BrokenSMTP)
    response = client.post('/campaigns/1/recipients/1/send-email')
    assert response.status_code == 400
    assert b'provider detail' not in response.data
    assert read_one(factory, 'SELECT sent_at FROM recipients WHERE id=1')['sent_at'] is None


def test_body_and_sender_rejections_do_not_change_existing_template(db_client):
    client, factory = db_client
    save(client)
    before = template_store.get_template(1)
    values = template_form() | {'csrf_token': csrf(client), 'subject': 'Injected\nBcc: other@example.com'}
    response = client.post('/templates/1/edit', data=values)
    assert b'single line' in response.data
    assert template_store.get_template(1) == before


def test_request_size_limit(db_client):
    client, factory = db_client
    response = client.post('/templates/new', data=b'x' * (32 * 1024 * 1024 + 1), content_type='application/octet-stream')
    assert response.status_code == 413


def test_preview_html_is_the_content_sent(campaign, monkeypatch):
    from html.parser import HTMLParser
    class PreviewParser(HTMLParser):
        content = None
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'iframe':
                self.content = values.get('srcdoc')
    client, factory = campaign
    save(client, image=(io.BytesIO(image_bytes()), 'illustration.png'))
    apply(client)
    client.post('/campaigns/1/launch')
    response = client.get('/campaigns/1/email-preview')
    parser = PreviewParser(); parser.feed(response.get_data(as_text=True))
    assert parser.content
    FakeSMTP.messages = []; FakeSMTP.events = []
    monkeypatch.setattr(email_service.smtplib, 'SMTP', FakeSMTP)
    assert client.post('/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client), 'authorized': 'on'}).status_code == 302
    html = FakeSMTP.messages[0].get_body(preferencelist=('html',)).get_content().rstrip()
    # Browser preview loads the same image by internal asset ID; MIME uses its CID.
    assert parser.content.replace('/template-assets/1', 'cid:campaign-illustration') == html


def test_archived_illustration_stays_inline_in_existing_campaign(campaign):
    client, factory = campaign
    save(client, image=(io.BytesIO(image_bytes()), 'illustration.png'))
    apply(client)
    assert client.post('/templates/1/edit', data=template_form() | {'csrf_token': csrf(client), 'remove_assets': '1'}).status_code == 302
    assert template_store.get_template(1)['assets'] == []
    assert template_store.get_asset(1)['kind'] == 'image'
    assert client.get('/template-assets/1').headers['Content-Disposition'].startswith('inline')
    campaign_row = read_one(factory, 'SELECT * FROM campaigns WHERE id=1')
    campaign_row['email_template'] = template_store.campaign_template(1)
    recipient = read_one(factory, 'SELECT * FROM recipients WHERE id=1')
    message = email_service.build_campaign_message(campaign_row, recipient)
    assert any(part.get('Content-ID') == '<campaign-illustration>' for part in message.walk())


def test_template_reuse_for_different_campaigns(campaign):
    client, factory = campaign
    save(client); apply(client)
    assert client.post('/campaigns/new', data={'campaign_name': 'Second campaign', 'campaign_template': 'security-notice',
                                             'start_date': '2026-10-13', 'authorized': 'on'}).status_code == 302
    client.post('/campaigns/2/recipients/new', data={'full_name': 'Sam User', 'email': 'sam@example.com'})
    client.post('/campaigns/2/recipients/2/approve')
    assert client.post('/campaigns/2/studio', data={'csrf_token': csrf(client), 'template_id': '1', 'authorized': 'on'}).status_code == 302
    first = client.get('/campaigns/1/email-preview').data
    second = client.get('/campaigns/2/email-preview').data
    assert b'Review Quarterly awareness' in first
    assert b'Review Second campaign' in second
    assert b'Hello Sam User' in second
    assert template_store.campaign_template(1) == template_store.campaign_template(2)


def test_integrity_failure_blocks_send_without_marking_sent(campaign, monkeypatch):
    client, factory = campaign
    save(client, attachments=(io.BytesIO(b'original'), 'guide.txt'))
    apply(client); client.post('/campaigns/1/launch'); client.get('/campaigns/1/email-preview')
    connection = factory()
    with connection.cursor() as cursor:
        cursor.execute('UPDATE template_assets SET data=%s WHERE id=1', (b'tampered',))
    connection.close()
    FakeSMTP.messages = []; FakeSMTP.events = []
    monkeypatch.setattr(email_service.smtplib, 'SMTP', FakeSMTP)
    response = client.post('/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client), 'authorized': 'on'})
    assert response.status_code == 400
    assert b'asset has changed' in response.data
    assert FakeSMTP.messages == []
    assert read_one(factory, 'SELECT sent_at FROM recipients WHERE id=1')['sent_at'] is None


def test_authenticated_identity_removal_blocks_delivery(campaign, monkeypatch):
    client, factory = campaign
    save(client, sender_email='training@example.com'); apply(client)
    client.post('/campaigns/1/launch'); client.get('/campaigns/1/email-preview')
    monkeypatch.setenv('SMTP_ALLOWED_FROM_EMAILS', '')
    FakeSMTP.messages = []; FakeSMTP.events = []
    monkeypatch.setattr(email_service.smtplib, 'SMTP', FakeSMTP)
    response = client.post('/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client), 'authorized': 'on'})
    assert response.status_code == 400
    assert FakeSMTP.messages == []


def test_schema_setup_repeatability_preserves_existing_campaign(campaign, monkeypatch):
    import database
    from pathlib import Path
    from sqlite_adapter import SQLiteConnection
    client, factory = campaign
    save(client); apply(client)
    campaign_before = read_one(factory, 'SELECT * FROM campaigns WHERE id=1')
    recipient_before = read_one(factory, 'SELECT * FROM recipients WHERE id=1')
    snapshot_before = template_store.campaign_template(1)
    connection = factory()
    if isinstance(connection, SQLiteConnection):
        connection.connection.executescript(Path(__file__).with_name('sqlite_schema.sql').read_text())
        connection.close()
    else:
        connection.close()
        database.initialize_database()
        database.initialize_database()
    assert read_one(factory, 'SELECT * FROM campaigns WHERE id=1') == campaign_before
    assert read_one(factory, 'SELECT * FROM recipients WHERE id=1') == recipient_before
    assert template_store.campaign_template(1) == snapshot_before


def test_missing_smtp_credentials_and_unauthorized_campaign_fail_closed(campaign, monkeypatch):
    client, factory = campaign
    client.post('/campaigns/1/launch')
    monkeypatch.delenv('SMTP_PASSWORD')
    response = client.post('/campaigns/1/recipients/1/send-email')
    assert response.status_code == 400 and b'SMTP_PASSWORD' in response.data
    connection = factory()
    with connection.cursor() as cursor:
        cursor.execute('UPDATE campaigns SET authorized=0 WHERE id=1')
    connection.close()
    response = client.post('/campaigns/1/recipients/1/send-email')
    assert response.status_code == 400 and b'authorization' in response.data.lower()
    assert read_one(factory, 'SELECT sent_at FROM recipients WHERE id=1')['sent_at'] is None


def test_five_attachments_and_remaining_limit_enforced(db_client):
    client, factory = db_client
    assert save(client, attachments=[(io.BytesIO(b'guide'), f'guide{i}.txt') for i in range(5)]).status_code == 302
    assert len(template_store.get_template(1)['assets']) == 5
    values = template_form() | {'csrf_token': csrf(client), 'attachments': (io.BytesIO(b'new'), 'new.txt')}
    assert b'five attachments' in client.post('/templates/1/edit', data=values).data
    assert len(template_store.get_template(1)['assets']) == 5


def test_unicode_body_limit_is_supported(db_client):
    client, factory = db_client
    assert save(client, body='🛡' * 20000).status_code == 302
    assert template_store.get_template(1)['body'] == '🛡' * 20000


def test_delivery_serializes_template_replacement(campaign, monkeypatch):
    """Replacing a campaign template during SMTP delivery cannot race the send."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from template_content import TemplateValidationError
    client, factory = campaign
    save(client); apply(client)
    save(client, subject='Replacement message')
    replacement = template_store.get_template(2)
    client.post('/campaigns/1/launch'); client.get('/campaigns/1/email-preview')
    token = csrf(client)
    smtp_entered = Event(); finish_smtp = Event(); apply_entered = Event(); apply_done = Event()
    class BlockingSMTP(FakeSMTP):
        def send_message(self, message):
            smtp_entered.set()
            assert finish_smtp.wait(5)
    monkeypatch.setattr(email_service.smtplib, 'SMTP', BlockingSMTP)
    def replace():
        apply_entered.set()
        try:
            template_store.apply_template(1, replacement)
        except TemplateValidationError as exc:
            return str(exc)
        finally:
            apply_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        delivery = pool.submit(client.post, '/campaigns/1/recipients/1/send-email', data={'csrf_token': token, 'authorized': 'on'})
        try:
            assert smtp_entered.wait(5)
            changing = pool.submit(replace)
            assert apply_entered.wait(5)
            assert not apply_done.wait(0.15)
        finally:
            finish_smtp.set()
        assert delivery.result(timeout=5).status_code == 302
        assert 'locked' in changing.result(timeout=5)
    assert template_store.campaign_template(1)['subject'] == template_form()['subject']


def test_invalid_tracking_configuration_fails_without_delivery(campaign, monkeypatch):
    client, factory = campaign
    save(client); apply(client)
    monkeypatch.setenv('BASE_URL', 'javascript:unsafe')
    response = client.get('/campaigns/1/email-preview')
    assert response.status_code == 400
    response = client.post('/templates/1/edit', data=template_form() | {'csrf_token': csrf(client), 'action': 'preview'})
    assert b'BASE_URL' in response.data
    response = client.post('/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client), 'authorized': 'on'})
    assert response.status_code == 400
    assert read_one(factory, 'SELECT sent_at FROM recipients WHERE id=1')['sent_at'] is None


def test_landing_has_no_credential_collection_and_rejects_posts(campaign):
    client, factory = campaign
    token = read_one(factory, 'SELECT tracking_token FROM recipients WHERE id=1')['tracking_token']
    response = client.get('/t/' + token)
    assert response.status_code == 200
    assert b'<input' not in response.data and b'<form' not in response.data
    assert client.post('/t/' + token, data={'password': 'never-store', 'otp': 'never-store'}).status_code == 405
    event = read_one(factory, 'SELECT * FROM tracking_events')
    assert event['event_type'] == 'link_clicked'
    assert 'password' not in event and 'otp' not in event


def test_campaign_creation_requires_authorization(db_client):
    client, factory = db_client
    response = client.post('/campaigns/new', data={'campaign_name': 'Unauthorized', 'campaign_template': 'basic-link', 'start_date': '2026-10-12'})
    assert response.status_code == 400
    assert read_one(factory, 'SELECT COUNT(*) AS total FROM campaigns')['total'] == 0
