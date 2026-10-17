"""Destructive sender actions require fresh operator credentials, never SMTP secrets."""
import pytest
import app as app_module
import sender_accounts as accounts
import template_store
from conftest import post, TEST_OPERATOR_PASSWORD, template_form
from test_sender_accounts import create, read, transport, values
from template_content import validate_template


def delete(client, identity, password=TEST_OPERATOR_PASSWORD):
    return post(client, f'/senders/{identity}/delete', data={'operator_password': password})


def test_current_operator_password_permanently_removes_credential(db_client):
    client, factory = db_client
    identity = create()
    before = accounts.get_account(identity, private=True)
    assert before['encrypted_password']
    assert delete(client, identity).status_code == 302
    row = accounts.get_account(identity, private=True)
    assert row['deleted'] and not row['enabled'] and not row['verified']
    assert row['encrypted_password'] is None and row['verification_digest'] is None
    assert read(factory, 'SELECT email FROM sender_accounts WHERE id=%s', (identity,))['email'] == before['email']
    assert client.get(f'/senders/{identity}/delete').status_code == 404


@pytest.mark.parametrize('password', ['', 'wrong-operator-password', 'isolated-test-password', 'x' * 4097])
def test_missing_or_incorrect_password_does_not_change_any_sender_fields(db_client, password, caplog):
    client, _ = db_client
    identity = create()
    before = accounts.get_account(identity, private=True)
    response = delete(client, identity, password)
    assert response.status_code == 400
    assert b'operator password' in response.data
    assert accounts.get_account(identity, private=True) == before
    if password:
        assert password.encode() not in response.data
        assert password not in caplog.text
    assert b'value="wrong-operator-password"' not in response.data


def test_password_field_absent_blocks_deletion(db_client):
    client, _ = db_client
    identity = create()
    assert post(client, f'/senders/{identity}/delete').status_code == 400
    assert not accounts.get_account(identity)['deleted']


def test_default_sender_protected_until_another_verified_default_selected(db_client, transport):
    client, _ = db_client
    identity = create(True)
    accounts.account_action(identity, 'default')
    before = accounts.get_account(identity, private=True)
    assert delete(client, identity).status_code == 400
    assert accounts.get_account(identity, private=True) == before
    assert next(a for a in accounts.list_accounts() if a['id'] == identity)['is_default']
    replacement = accounts.save_account(values(email='replacement@sender.example'), 'isolated-test-password')
    with pytest.raises(accounts.SenderAccountError):
        accounts.account_action(replacement, 'default')
    assert delete(client, identity).status_code == 400
    accounts.verify_account(replacement)
    accounts.account_action(replacement, 'default')
    assert delete(client, identity).status_code == 302


def test_environment_configured_identity_cannot_be_deleted(db_client):
    client, _ = db_client
    accounts.synchronize_configured()
    identity = next(a['id'] for a in accounts.list_accounts() if a['source_key'])
    before = accounts.get_account(identity, private=True)
    response = delete(client, identity)
    assert response.status_code == 400 and b'server configuration' in response.data
    assert accounts.get_account(identity, private=True) == before


def test_active_campaign_blocks_deletion_then_historical_data_survives(campaign, transport):
    client, factory = campaign
    identity = create(True)
    template_id = template_store.save_template(validate_template(template_form() | {'sender_account_id':identity,'sender_email':'training@sender.example'}), [])
    template_store.apply_template(1, template_store.get_template(template_id))
    assert post(client, '/campaigns/1/launch').status_code == 302
    before = accounts.get_account(identity, private=True)
    snapshot = template_store.campaign_template(1)
    template = template_store.get_template(template_id)
    response = delete(client, identity)
    assert response.status_code == 400 and b'active campaign' in response.data
    assert accounts.get_account(identity, private=True) == before
    connection = factory()
    with connection.cursor() as cursor:
        cursor.execute("UPDATE campaigns SET status='completed' WHERE id=1")
    connection.close()
    assert delete(client, identity).status_code == 302
    assert template_store.campaign_template(1) == snapshot
    assert template_store.get_template(template_id) == template
    for url in ['/campaigns/1/email-preview', '/campaigns/1/results', f'/templates/{template_id}/edit']:
        assert client.get(url).status_code == 200
    assert b'training@sender.example' in client.get('/campaigns/1/email-preview').data


def test_disable_remains_reversible_without_password(db_client, transport):
    client, _ = db_client
    identity = create(True)
    secret = accounts.get_account(identity, private=True)['encrypted_password']
    assert post(client, f'/senders/{identity}/disable').status_code == 302
    assert not accounts.get_account(identity)['enabled']
    assert accounts.get_account(identity, private=True)['encrypted_password'] == secret
    assert post(client, f'/senders/{identity}/enable').status_code == 302
    assert accounts.get_account(identity)['enabled'] and not accounts.get_account(identity)['verified']


def test_confirmation_is_read_only_escaped_and_access_protected(db_client):
    client, _ = db_client
    identity = accounts.save_account(values(display_name='<script>sender</script>'), 'isolated-test-password')
    before = accounts.get_account(identity, private=True)
    response = client.get(f'/senders/{identity}/delete')
    assert response.status_code == 200
    assert b'&lt;script&gt;sender&lt;/script&gt;' in response.data
    assert b'training@sender.example' in response.data
    assert b'permanently removed' in response.data and b'Historical campaign records' in response.data
    assert b'type="password" name="operator_password"' in response.data
    assert before['encrypted_password'].encode() not in response.data
    assert accounts.get_account(identity, private=True) == before
    anonymous = app_module.app.test_client()
    assert anonymous.get(f'/senders/{identity}/delete').status_code == 302
    assert anonymous.post(f'/senders/{identity}/delete', data={'operator_password':TEST_OPERATOR_PASSWORD}).status_code == 401
    assert client.post(f'/senders/{identity}/delete', data={'operator_password':TEST_OPERATOR_PASSWORD}).status_code == 400


def test_deletion_password_attempts_are_throttled(db_client):
    client, _ = db_client
    identity = create()
    for _ in range(4):  # Initial operator sign-in already used one attempt.
        assert delete(client, identity, 'wrong-password').status_code == 400
    assert delete(client, identity).status_code == 429
    assert not accounts.get_account(identity)['deleted']
