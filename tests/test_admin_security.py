"""Authentication, global CSRF, private assets, and public awareness regressions."""
import hashlib
import io
import re
import time

import pytest
from werkzeug.security import generate_password_hash

import app as app_module
import security
import studio
from conftest import TEST_OPERATOR_PASSWORD, login_operator, post

ADMIN_GETS = ['/', '/campaigns', '/campaigns/new', '/recipients', '/analytics', '/settings',
              '/campaigns/1/recipients', '/campaigns/1/recipients/new', '/campaigns/1/results',
              '/campaigns/1/email-preview', '/campaigns/1/studio', '/templates', '/templates/new',
              '/templates/1/edit', '/senders', '/senders/new', '/senders/1/edit']
MUTATIONS = ['/campaigns/new', '/campaigns/1/recipients/new', '/campaigns/1/recipients/1/approve',
             '/campaigns/1/launch', '/campaigns/1/recipients/1/send-email', '/templates/new',
             '/templates/1/edit', '/campaigns/1/studio', '/senders/new', '/senders/1/edit',
             '/senders/1/test', '/senders/1/default', '/senders/1/disable', '/senders/1/enable',
             '/senders/1/delete', '/operator/logout']


def fail_database():
    pytest.fail('Unauthenticated or CSRF-invalid request reached the database')


def token(client):
    client.get('/operator/login')
    with client.session_transaction() as state:
        return state['studio_csrf']


@pytest.mark.parametrize('url', ADMIN_GETS)
def test_admin_pages_require_login_before_database(monkeypatch, url):
    monkeypatch.setattr(app_module, 'get_db_connection', fail_database)
    monkeypatch.setattr(studio, 'get_db_connection', fail_database)
    response = app_module.app.test_client().get(url)
    assert response.status_code == 302
    assert response.headers['Location'] == '/operator/login'
    assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('url', MUTATIONS)
def test_anonymous_admin_mutations_denied(monkeypatch, url):
    monkeypatch.setattr(app_module, 'get_db_connection', fail_database)
    monkeypatch.setattr(studio, 'get_db_connection', fail_database)
    response = app_module.app.test_client().post(url, data={'csrf_token':'untrusted'})
    assert response.status_code == 401


@pytest.mark.parametrize('url', MUTATIONS + ['/operator/login'])
@pytest.mark.parametrize('supplied', [None, 'wrong-token'])
def test_all_mutations_reject_missing_or_wrong_csrf(monkeypatch, url, supplied):
    client = login_operator(app_module.app.test_client())
    monkeypatch.setattr(app_module, 'get_db_connection', fail_database)
    monkeypatch.setattr(studio, 'get_db_connection', fail_database)
    response = client.post(url, data={} if supplied is None else {'csrf_token':supplied})
    assert response.status_code == 400 and b'Invalid form token' in response.data


def test_login_requires_csrf_and_does_not_echo_credentials():
    client = app_module.app.test_client()
    assert client.post('/operator/login',data={'username':'test-operator','password':TEST_OPERATOR_PASSWORD}).status_code == 400
    original = token(client)
    with client.session_transaction() as state:
        state['reviewed_email'] = ['stale-review']
    response = client.post('/operator/login', data={'csrf_token':original,'username':'test-operator','password':TEST_OPERATOR_PASSWORD})
    assert response.status_code == 302
    with client.session_transaction() as state:
        assert state['studio_csrf'] != original
        assert 'reviewed_email' not in state
        assert 'password' not in state and 'username' not in state
        assert TEST_OPERATOR_PASSWORD not in str(dict(state))
    assert client.post('/operator/logout',data={'csrf_token':original}).status_code == 400
    assert client.get('/operator/login').status_code == 302


def test_bad_credentials_generic_and_never_echoed():
    client = app_module.app.test_client()
    csrf = token(client)
    for username in ['unknown-user','test-operator']:
        response = client.post('/operator/login',data={'csrf_token':csrf,'username':username,'password':'not-the-operator-password'})
        assert response.status_code == 401 and b'Invalid operator credentials' in response.data
        assert b'not-the-operator-password' not in response.data and username.encode() not in response.data


def test_login_throttle_and_window_expiry(monkeypatch):
    client = app_module.app.test_client()
    csrf = token(client)
    for _ in range(5):
        assert client.post('/operator/login',data={'csrf_token':csrf,'username':'unknown','password':'wrong'}).status_code == 401
    response = client.post('/operator/login',data={'csrf_token':csrf,'username':'test-operator','password':TEST_OPERATOR_PASSWORD})
    assert response.status_code == 429 and response.headers['Retry-After'] == '600'
    now = time.monotonic()
    monkeypatch.setattr(security.time,'monotonic',lambda:now+601)
    assert client.post('/operator/login',data={'csrf_token':csrf,'username':'test-operator','password':TEST_OPERATOR_PASSWORD}).status_code == 302


@pytest.mark.parametrize('encoded', ['', 'plain$wrong$hash', 'pbkdf2:sha256:1$salt$1234'])
def test_missing_or_weak_operator_configuration_fails_closed(monkeypatch, encoded):
    monkeypatch.setitem(app_module.app.config,'ADMIN_PASSWORD_HASH',encoded)
    client = app_module.app.test_client()
    response = client.get('/operator/login')
    assert response.status_code == 503
    assert b'Operator access is not configured' in response.data
    assert client.get('/').status_code == 302
    assert client.get('/template-assets/1').status_code == 401


def test_production_requires_secure_server_configuration(monkeypatch):
    monkeypatch.setitem(app_module.app.config,'APP_ENV','production')
    monkeypatch.setitem(app_module.app.config,'PRODUCTION_AUTH_READY',False)
    assert app_module.app.test_client().get('/operator/login').status_code == 503


def test_logout_revokes_copied_cookie_and_get_cannot_log_out(db_client):
    client, _ = db_client
    copied = client.get_cookie('session').value
    assert client.get('/operator/logout').status_code == 405
    with client.session_transaction() as state:
        csrf = state['studio_csrf']
    assert client.post('/operator/logout',data={'csrf_token':csrf}).status_code == 302
    attacker = app_module.app.test_client()
    attacker.set_cookie('session',copied)
    assert attacker.get('/').status_code == 302
    assert attacker.get('/template-assets/1').status_code == 401


def test_absolute_session_expiration(db_client):
    client, _ = db_client
    with client.session_transaction() as state:
        state['operator_login_at'] -= 60 * (app_module.app.config['OPERATOR_SESSION_MINUTES'] + 1)
        digest = hashlib.sha256(state['operator_nonce'].encode()).hexdigest()
        app_module.app.extensions['operator_sessions']['active'][digest] = state['operator_login_at']
    assert client.get('/').status_code == 302
    assert client.post('/campaigns/1/launch').status_code == 401


def test_password_rotation_revokes_sessions(db_client,monkeypatch):
    client, _ = db_client
    monkeypatch.setitem(app_module.app.config,'ADMIN_PASSWORD_HASH',generate_password_hash('isolated-new-operator-password'))
    assert client.get('/').status_code == 302


def test_unknown_session_nonce_fails_closed(db_client):
    client, _ = db_client
    app_module.app.extensions['operator_sessions']['active'].clear()
    assert client.get('/').status_code == 302


def test_login_ignores_external_redirect_targets(db_client):
    client = app_module.app.test_client()
    csrf = token(client)
    response=client.post('/operator/login?next=https://attacker.example',data={'csrf_token':csrf,'username':'test-operator','password':TEST_OPERATOR_PASSWORD})
    assert response.status_code == 302 and response.headers['Location'] == '/'


def test_private_assets_protected_before_lookup_and_public_token_no_access(campaign,monkeypatch):
    from test_template_studio import save
    client, _=campaign
    assert save(client,attachments=(io.BytesIO(b'private guide'),'guide.txt')).status_code == 302
    authenticated=client.get('/template-assets/1')
    assert authenticated.data == b'private guide'
    assert authenticated.headers['Cache-Control']=='no-store'
    assert authenticated.headers['X-Content-Type-Options']=='nosniff'
    assert 'sandbox' in authenticated.headers['Content-Security-Policy']
    assert client.get('/template-assets/99999').status_code == 404
    monkeypatch.setattr(studio,'get_asset',lambda *args:pytest.fail('Anonymous asset lookup'))
    anonymous=app_module.app.test_client()
    for url in ['/template-assets/1','/template-assets/99999','/template-assets/1?tracking_token=public-token']:
        assert anonymous.get(url).status_code == 401
        assert anonymous.head(url).status_code == 401


def test_tracking_remains_public_without_operator_configuration(campaign,monkeypatch):
    from tests_helper import read_one
    client, factory=campaign
    post(client,'/campaigns/1/launch')
    tracking_token=read_one(factory,'SELECT tracking_token FROM recipients WHERE id=1')['tracking_token']
    monkeypatch.setitem(app_module.app.config,'ADMIN_PASSWORD_HASH','')
    public=app_module.app.test_client()
    for _ in range(2):
        response=public.get('/t/'+tracking_token)
        assert response.status_code==200 and b'<input' not in response.data and b'<form' not in response.data
        assert b'Main navigation' not in response.data
    assert read_one(factory,'SELECT COUNT(*) AS total FROM tracking_events')['total']==1
    assert public.post('/t/'+tracking_token,data={'password':'never-collect'}).status_code == 405


def test_csrf_bound_to_each_operator_session(db_client):
    first, _=db_client
    second=login_operator(app_module.app.test_client())
    with first.session_transaction() as state:csrf=state['studio_csrf']
    assert second.post('/campaigns/new',data={'csrf_token':csrf}).status_code==400


def test_csrf_header_supported_for_authenticated_client(db_client):
    client, _=db_client
    with client.session_transaction() as state:csrf=state['studio_csrf']
    assert client.post('/campaigns/new',headers={'X-CSRF-Token':csrf},data={}).status_code==400
    response=client.post('/campaigns/new',headers={'X-CSRF-Token':csrf},data={'campaign_name':'Header token campaign','campaign_template':'basic-link','start_date':'2026-10-16','authorized':'on'})
    assert response.status_code==302


def test_untrusted_host_is_rejected():
    assert app_module.app.test_client().get('/',base_url='http://attacker.example').status_code==400


def test_configured_password_hash_and_secrets_absent_from_responses(db_client):
    client, _=db_client
    for url in ['/operator/login','/','/settings']:
        response=client.get(url)
        assert app_module.app.config['ADMIN_PASSWORD_HASH'].encode() not in response.data
        assert TEST_OPERATOR_PASSWORD.encode() not in response.data


def test_login_password_hash_cli_rejects_short_password_and_generates_strong_hash():
    runner=app_module.app.test_cli_runner()
    failed=runner.invoke(args=['hash-operator-password'],input='short\nshort\n')
    assert failed.exit_code!=0
    result=runner.invoke(args=['hash-operator-password'],input=TEST_OPERATOR_PASSWORD+'\n'+TEST_OPERATOR_PASSWORD+'\n')
    assert result.exit_code==0 and 'scrypt:' in result.output
    assert TEST_OPERATOR_PASSWORD not in result.output
