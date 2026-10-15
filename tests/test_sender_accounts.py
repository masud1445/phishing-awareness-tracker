from conftest import post, login_operator
"""Account integration tests use isolated SQL and a non-network SMTP double."""
from contextlib import contextmanager
import os
import smtplib

import pytest
from cryptography.fernet import Fernet
from conftest import csrf, template_form
import email_service
import sender_accounts as accounts
import template_store
from template_content import validate_template, TemplateValidationError


def values(**overrides):
    return dict(display_name='Training Operations', email='training@sender.example', provider='custom',
                smtp_host='smtp.sender.example', smtp_port='587', tls_mode='starttls',
                smtp_username='training@sender.example') | overrides


def read(factory, query, args=()):
    connection = factory()
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, args)
            return cursor.fetchone()
    finally:
        connection.close()


class Transport:
    def __init__(self):
        self.events = []; self.messages = []; self.settings = []
    def mail(self, address):
        self.events.append(('mail', address)); return 250, b'Accepted'
    def rset(self):
        self.events.append(('rset',))
    def send_message(self, message, **envelope):
        self.messages.append((message, envelope))


@pytest.fixture
def transport(monkeypatch):
    server = Transport()
    @contextmanager
    def connect(settings):
        server.settings.append(settings)
        yield server
    monkeypatch.setattr(accounts, 'open_account_connection', connect)
    return server


def create(verified=False):
    account_id = accounts.save_account(values(), 'isolated-test-password')
    if verified:
        accounts.verify_account(account_id)
    return account_id


def test_add_encrypts_and_never_discloses_secret(db_client):
    client, factory = db_client
    token = csrf(client)
    response = client.post('/senders/new', data=values() | {'csrf_token': token, 'smtp_password': 'isolated-test-password'})
    assert response.status_code == 302
    row = read(factory, 'SELECT * FROM sender_accounts WHERE source_key IS NULL')
    assert row['encrypted_password'] != 'isolated-test-password'
    assert Fernet(os.environ['SENDER_ENCRYPTION_KEY'].encode()).decrypt(row['encrypted_password'].encode()) == b'isolated-test-password'
    assert not row['verified']
    assert 'encrypted_password' not in accounts.get_account(row['id'])
    for url in ('/senders', f"/senders/{row['id']}/edit", '/templates/new'):
        response = client.get(url)
        assert b'isolated-test-password' not in response.data
        assert row['encrypted_password'].encode() not in response.data
        assert os.environ['SENDER_ENCRYPTION_KEY'].encode() not in response.data
    assert b'value="isolated-test-password"' not in response.data


def test_edit_blank_retains_ciphertext_and_requires_reverification(db_client, transport):
    client, factory = db_client
    identity = create(True)
    accounts.account_action(identity, 'default')
    old = accounts.get_account(identity, private=True)
    response = post(client, f'/senders/{identity}/edit', data=values(display_name='Updated team') | {'csrf_token': csrf(client), 'smtp_password': ''})
    assert response.status_code == 302
    row = accounts.get_account(identity, private=True)
    assert row['encrypted_password'] == old['encrypted_password']
    assert row['display_name'] == 'Updated team' and not row['verified']
    assert not any(a['is_default'] for a in accounts.list_accounts())
    with pytest.raises(accounts.SenderAccountError): accounts.require_verified(identity)


def test_replace_password_reencrypts(db_client, transport):
    identity = create(True)
    accounts.save_account(values(), 'isolated-replacement-password', identity)
    assert accounts.connection_settings(accounts.get_account(identity, private=True))['password'] == 'isolated-replacement-password'
    assert not accounts.get_account(identity)['verified']


def test_multiple_accounts_and_one_default(db_client, transport):
    first = create(True)
    second = accounts.save_account(values(email='another@sender.example'), 'isolated-test-password')
    accounts.verify_account(second)
    accounts.account_action(first, 'default'); accounts.account_action(second, 'default')
    assert [a['id'] for a in accounts.list_accounts() if a['is_default']] == [second]
    client, _ = db_client
    response = client.get('/templates/new')
    assert f'value="{second}" data-email="another@sender.example" selected'.encode() in response.data
    assert b'+ Add sender' in response.data and b'Manage senders' in response.data


def test_unverified_rejected_verified_selectable(db_client, transport):
    identity = create()
    form = template_form() | {'sender_account_id': identity, 'sender_email': 'training@sender.example'}
    with pytest.raises(accounts.SenderAccountError): validate_template(form)
    with pytest.raises(accounts.SenderAccountError): accounts.account_action(identity, 'default')
    assert identity not in [a['id'] for a in accounts.selectable_accounts()]
    accounts.verify_account(identity)
    assert validate_template(form)['sender_account_id'] == identity
    assert identity in [a['id'] for a in accounts.selectable_accounts()]
    assert transport.messages == []
    assert transport.events == [('mail', 'training@sender.example'), ('rset',)]
    with pytest.raises(TemplateValidationError):
        validate_template(form | {'sender_email': 'unverified@sender.example'})


@pytest.mark.parametrize('action', ['disable', 'delete'])
def test_disabled_deleted_block_delivery_and_keep_history(campaign, transport, action):
    client, factory = campaign
    identity = create(True)
    template = validate_template(template_form() | {'sender_account_id': identity, 'sender_email': 'training@sender.example'})
    template_id = template_store.save_template(template, [])
    template_store.apply_template(1, template_store.get_template(template_id))
    post(client, '/campaigns/1/launch'); client.get('/campaigns/1/email-preview')
    accounts.account_action(identity, action)
    response = client.get('/campaigns/1/email-preview')
    assert response.status_code == 200 and b'training@sender.example' in response.data
    assert b'history is preserved' in response.data
    response = post(client, '/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client), 'authorized': 'on'})
    assert response.status_code == 400 and not transport.messages
    assert read(factory, 'SELECT sent_at FROM recipients WHERE id=1')['sent_at'] is None
    assert template_store.campaign_template(1)['sender_email'] == 'training@sender.example'
    if action == 'delete':
        assert accounts.get_account(identity, private=True)['encrypted_password'] is None
        assert identity not in [a['id'] for a in accounts.list_accounts()]
    else:
        accounts.account_action(identity, 'enable')
        assert not accounts.get_account(identity)['verified']


def test_preview_and_delivery_selected_identity_consistent(campaign, transport):
    client, factory = campaign
    identity = create(True)
    token = csrf(client)
    form = template_form() | {'sender_account_id': identity, 'sender_email': 'training@sender.example', 'csrf_token': token}
    assert post(client, '/templates/new', data=form).status_code == 302
    assert post(client, '/campaigns/1/studio', data={'template_id': 1, 'authorized': 'on', 'csrf_token': token}).status_code == 302
    post(client, '/campaigns/1/launch')
    response = client.get('/campaigns/1/email-preview')
    assert b'training@sender.example' in response.data
    assert post(client, '/campaigns/1/recipients/1/send-email', data={'csrf_token': token, 'authorized': 'on'}).status_code == 302
    message, envelope = transport.messages[0]
    assert message['From'] == 'Awareness Team <training@sender.example>'
    assert envelope['from_addr'] == 'training@sender.example'
    assert transport.settings[-1]['smtp_host'] == 'smtp.sender.example'
    assert transport.settings[-1]['smtp_username'] == 'training@sender.example'
    assert message.get_body(preferencelist=('plain',)).get_content().strip() in response.get_data(as_text=True).replace('&lt;', '<').replace('&gt;', '>').replace('&#34;', '"')


def test_campaign_can_override_template_sender(campaign, transport):
    client, _ = campaign
    identity = create(True)
    legacy = validate_template(template_form())
    template_store.save_template(legacy, [])
    response = post(client, '/campaigns/1/studio', data={'template_id': 1, 'sender_account_id': identity, 'authorized': 'on', 'csrf_token': csrf(client)})
    assert response.status_code == 302
    assert template_store.campaign_template(1)['sender_account_id'] == identity
    assert template_store.campaign_template(1)['sender_email'] == 'training@sender.example'


def test_account_changes_invalidate_previous_review(campaign, transport):
    client, _ = campaign
    identity = create(True)
    template_store.save_template(validate_template(template_form() | {'sender_account_id': identity, 'sender_email': 'training@sender.example'}), [])
    template_store.apply_template(1, template_store.get_template(1))
    post(client, '/campaigns/1/launch'); client.get('/campaigns/1/email-preview')
    accounts.verify_account(identity)
    response = post(client, '/campaigns/1/recipients/1/send-email', data={'csrf_token': csrf(client), 'authorized': 'on'})
    assert response.status_code == 400 and b'Preview' in response.data
    assert not transport.messages


def test_configured_sender_preserved_first_and_password_not_copied(db_client, transport, monkeypatch):
    accounts.synchronize_configured(); create()
    rows = accounts.list_accounts()
    assert rows[0]['email'] == 'awareness@example.com' and rows[0]['source_key']
    assert accounts.get_account(rows[0]['id'], private=True)['encrypted_password'] is None
    accounts.verify_account(rows[0]['id'])
    accounts.require_verified(rows[0]['id'])
    monkeypatch.setenv('SMTP_PASSWORD', 'isolated-changed-password')
    with pytest.raises(accounts.SenderAccountError): accounts.require_verified(rows[0]['id'])
    accounts.synchronize_configured()
    assert len(accounts.list_accounts()) == 3


def test_missing_wrong_key_fail_closed_without_leak(db_client, monkeypatch):
    identity = create()
    monkeypatch.setenv('SENDER_ENCRYPTION_KEY', Fernet.generate_key().decode())
    with pytest.raises(accounts.SenderAccountError, match='cannot be decrypted'):
        accounts.connection_settings(accounts.get_account(identity, private=True))
    monkeypatch.delenv('SENDER_ENCRYPTION_KEY')
    with pytest.raises(accounts.SenderAccountError, match='Configure a valid'):
        accounts.save_account(values(), 'isolated-test-password')


def test_failed_retest_revokes_verified_state_without_exposing_response(db_client, transport, monkeypatch):
    identity = create(True)
    @contextmanager
    def failing(settings):
        raise smtplib.SMTPAuthenticationError(535, b'isolated-test-password')
        yield
    monkeypatch.setattr(accounts, 'open_account_connection', failing)
    client, _ = db_client
    response = post(client, f'/senders/{identity}/test', data={'csrf_token': csrf(client)})
    assert response.status_code == 400 and b'isolated-test-password' not in response.data
    assert not accounts.get_account(identity)['verified']


@pytest.mark.parametrize('provider,host,port,mode', [('gmail','smtp.gmail.com',465,'ssl'), ('outlook','smtp.office365.com',587,'starttls'), ('custom','smtp.sender.example',587,'starttls')])
def test_provider_settings(provider, host, port, mode):
    result = accounts.validate_account(values(provider=provider, smtp_port=port, tls_mode=mode))
    assert result['smtp_host'] == host


@pytest.mark.parametrize('overrides', [{'tls_mode':'none'}, {'smtp_port':0}, {'smtp_port':65536}, {'smtp_host':'https://evil.example/path'}, {'email':'a@example.com\r\nBcc:b@example.com'}, {'provider':'other'}, {'provider':'outlook','smtp_port':465,'tls_mode':'ssl'}])
def test_unsafe_settings_rejected(overrides):
    with pytest.raises(TemplateValidationError): accounts.validate_account(values(**overrides))


@pytest.mark.parametrize('address', ['127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1'])
def test_private_smtp_destination_rejected(monkeypatch, address):
    monkeypatch.setattr(accounts.socket, 'getaddrinfo', lambda *a, **k: [(2,1,6,'',(address,587))])
    with pytest.raises(accounts.SenderAccountError): accounts.safe_destination('smtp.example.com',587)


def test_management_csrf_and_validation_never_echo_password(db_client):
    client, _ = db_client
    assert client.post('/senders/new', data=values()).status_code == 400
    response = post(client, '/senders/new', data=values(email='invalid') | {'smtp_password':'isolated-test-password','csrf_token':csrf(client)})
    assert b'isolated-test-password' not in response.data
    identity = create()
    assert client.post(f'/senders/{identity}/delete').status_code == 400
    assert accounts.get_account(identity)['deleted'] == 0
    assert post(client, f'/senders/{identity}/delete', data={'csrf_token':csrf(client)}).status_code == 302
    assert client.get(f'/senders/{identity}/edit').status_code == 404


@pytest.mark.parametrize('mode', ['starttls', 'ssl'])
def test_connection_uses_checked_address_tls_hostname_and_no_email(monkeypatch, mode):
    events = []
    monkeypatch.setattr(accounts, 'safe_destination', lambda host, port: '8.8.8.8')
    class SMTP:
        def __init__(self, **kwargs):
            events.append(('create', kwargs))
        def connect(self, host, port):
            assert self.checked_address == '8.8.8.8' and self._host == 'smtp.sender.example'
            events.append(('connect', host, port))
        def starttls(self, context):
            assert context.check_hostname and context.verify_mode == accounts.ssl.CERT_REQUIRED
            events.append(('tls',))
        def login(self, username, password):
            events.append(('login', username))
        def quit(self): events.append(('quit',))
    monkeypatch.setattr(accounts, 'PinnedSMTP', SMTP)
    monkeypatch.setattr(accounts, 'PinnedSMTPSSL', SMTP)
    settings = accounts.validate_account(values(tls_mode=mode)) | {'password':'isolated-test-password'}
    with accounts.open_account_connection(settings): pass
    assert ('login','training@sender.example') in events
    assert (('tls',) in events) == (mode == 'starttls')
    if mode == 'ssl': assert events[0][1]['context'].check_hostname
    assert events[-1] == ('quit',)


def test_pinned_socket_does_not_resolve_again(monkeypatch):
    calls = []
    monkeypatch.setattr(accounts.socket, 'create_connection', lambda target, timeout, source: calls.append(target) or object())
    smtp = accounts.PinnedSMTP(timeout=20)
    smtp.checked_address = '8.8.8.8'
    smtp._get_socket('smtp.sender.example', 587, 20)
    assert calls == [('8.8.8.8',587)]


def test_mixed_public_private_dns_and_resolution_failure_rejected(monkeypatch):
    monkeypatch.setattr(accounts.socket, 'getaddrinfo', lambda *a, **k: [(2,1,6,'',('8.8.8.8',587)),(2,1,6,'',('127.0.0.1',587))])
    with pytest.raises(accounts.SenderAccountError): accounts.safe_destination('smtp.example.com',587)
    def fail(*args, **kwargs): raise OSError('isolated transport details')
    monkeypatch.setattr(accounts.socket, 'getaddrinfo', fail)
    with pytest.raises(accounts.SenderAccountError, match='could not be resolved'): accounts.safe_destination('smtp.example.com',587)


def test_server_rejects_sender_verification(db_client, transport, monkeypatch):
    identity = create()
    monkeypatch.setattr(transport, 'mail', lambda address: (550,b'isolated-test-password'))
    with pytest.raises(accounts.SenderAccountError, match='did not accept'): accounts.verify_account(identity)
    assert not accounts.get_account(identity)['verified'] and not transport.messages


def test_account_changed_during_verification_not_verified(db_client, transport, monkeypatch):
    identity = create()
    def during(address):
        accounts.save_account(values(display_name='Concurrent update'), '', identity)
        return 250,b'Accepted'
    monkeypatch.setattr(transport,'mail',during)
    with pytest.raises(accounts.SenderAccountError, match='changed during'): accounts.verify_account(identity)
    assert not accounts.get_account(identity)['verified']


def test_account_endpoints_default_disable_enable_and_delete(db_client, transport):
    client, _ = db_client
    identity = create()
    token = csrf(client)
    assert post(client, f'/senders/{identity}/test',data={'csrf_token':token}).status_code == 302
    assert post(client, f'/senders/{identity}/default',data={'csrf_token':token}).status_code == 302
    assert accounts.list_accounts()[-1]['is_default']
    assert post(client, f'/senders/{identity}/disable',data={'csrf_token':token}).status_code == 302
    assert b'>Delete</button>' in client.get('/senders').data
    assert post(client, f'/senders/{identity}/enable',data={'csrf_token':token}).status_code == 302
    assert not accounts.get_account(identity)['verified']
    assert post(client, f'/senders/{identity}/default',data={'csrf_token':token}).status_code == 400
    assert post(client, f'/senders/{identity}/delete',data={'csrf_token':token}).status_code == 302


@pytest.mark.parametrize('change', ['approved', 'status', 'authorized'])
def test_managed_sender_retains_delivery_guards(db_client, transport, change):
    identity = create(True)
    campaign = {'id':1,'campaign_name':'Awareness','status':'active','authorized':1,'email_template': validate_template(template_form() | {'sender_account_id':identity,'sender_email':'training@sender.example'})}
    recipient = {'id':1,'campaign_id':1,'approved':1,'full_name':'Alex','email':'alex@example.com','tracking_token':'isolated-token'}
    if change == 'approved': recipient['approved'] = 0
    elif change == 'status': campaign['status'] = 'draft'
    else: campaign['authorized'] = 0
    with pytest.raises(email_service.EmailDeliveryError): email_service.send_campaign_email(campaign,recipient)
    assert not transport.messages


def test_old_studio_snapshot_without_sender_id_still_previews_and_delivers(campaign, monkeypatch):
    import json
    from test_template_studio import FakeSMTP
    client, factory = campaign
    template_store.save_template(validate_template(template_form()), [])
    template_store.apply_template(1, template_store.get_template(1))
    snapshot = template_store.campaign_template(1)
    snapshot.pop('sender_account_id')
    connection = factory()
    with connection.cursor() as cursor:
        cursor.execute('UPDATE campaign_email_settings SET snapshot=%s WHERE campaign_id=1', (json.dumps(snapshot),))
    connection.close()
    FakeSMTP.messages = []; FakeSMTP.events = []
    monkeypatch.setattr(email_service.smtplib,'SMTP',FakeSMTP)
    post(client, '/campaigns/1/launch')
    assert client.get('/campaigns/1/email-preview').status_code == 200
    response = post(client, '/campaigns/1/recipients/1/send-email',data={'csrf_token':csrf(client),'authorized':'on'})
    assert response.status_code == 302 and len(FakeSMTP.messages) == 1
    assert 'awareness@example.com' in FakeSMTP.messages[0]['From']


def test_additive_sender_column_migration_preserves_old_template(db_client):
    import database
    from sqlite_adapter import SQLiteConnection
    client, factory = db_client
    connection = factory()
    with connection.cursor() as cursor:
        cursor.execute('ALTER TABLE email_templates DROP COLUMN sender_account_id')
        form = template_form()
        fields = ('name','subject','body','sender_name','sender_email','reference_link')
        cursor.execute('INSERT INTO email_templates (name,subject,body,sender_name,sender_email,reference_link) VALUES (%s,%s,%s,%s,%s,%s)',tuple(form[f] for f in fields))
    sqlite = isinstance(connection,SQLiteConnection)
    connection.close()
    if sqlite:
        connection = factory()
        with connection.cursor() as cursor: cursor.execute('ALTER TABLE email_templates ADD COLUMN sender_account_id INTEGER NULL')
        connection.close()
    else:
        database.initialize_database()
        database.initialize_database()
    assert template_store.get_template(1)['sender_account_id'] is None
    assert template_store.get_template(1)['subject'] == template_form()['subject']


def test_delivery_locks_selected_sender_against_disable(campaign, transport, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    client, factory = campaign
    identity = create(True)
    template_store.save_template(validate_template(template_form() | {'sender_account_id':identity,'sender_email':'training@sender.example'}),[])
    template_store.apply_template(1,template_store.get_template(1))
    post(client, '/campaigns/1/launch'); client.get('/campaigns/1/email-preview')
    token = csrf(client)
    sending = Event(); release = Event(); disabling = Event(); disabled = Event()
    def send(message, **kwargs):
        sending.set(); assert release.wait(5)
    monkeypatch.setattr(transport,'send_message',send)
    def disable():
        disabling.set()
        try: accounts.account_action(identity,'disable')
        finally: disabled.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        delivery = pool.submit(client.post,'/campaigns/1/recipients/1/send-email',data={'csrf_token':token,'authorized':'on'})
        try:
            assert sending.wait(5)
            change = pool.submit(disable)
            assert disabling.wait(5)
            assert not disabled.wait(.15)
        finally:
            release.set()
        assert delivery.result(timeout=5).status_code == 302
        change.result(timeout=5)
    assert accounts.get_account(identity)['enabled'] == 0
