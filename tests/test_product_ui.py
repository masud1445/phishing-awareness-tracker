from conftest import post, login_operator
import json
import os
import pytest
import app as app_module
import email_service
import template_store
from conftest import csrf, template_form
from template_content import validate_template, render_email, TemplateValidationError
from test_template_studio import FakeSMTP


@pytest.mark.parametrize('url', ['/','/campaigns','/recipients','/analytics','/settings','/campaigns/new','/templates','/templates/new','/senders','/senders/new'])
def test_workspace_pages_render_shared_shell_and_no_secrets(db_client, url):
    client, _ = db_client
    response = client.get(url)
    assert response.status_code == 200
    assert b'id="main-content"' in response.data
    assert b'aria-label="Main navigation"' in response.data
    assert os.environ['SMTP_PASSWORD'].encode() not in response.data
    assert os.environ['SENDER_ENCRYPTION_KEY'].encode() not in response.data


def test_analytics_counts_real_approved_recipients_and_deduplicates(campaign):
    client, factory = campaign
    post(client, '/campaigns/1/recipients/new',data={'full_name':'Not approved','email':'pending@example.com'})
    connection = factory()
    with connection.cursor() as cursor:
        for recipient in [1,1,2]:
            cursor.execute("INSERT INTO tracking_events (campaign_id,recipient_id,event_type) VALUES (1,%s,'link_clicked')",(recipient,))
    connection.close()
    overview = app_module.overview_context()
    assert overview['total_recipients'] == 1 and overview['link_clicks'] == 1
    assert overview['awareness_score'] == 0.0
    assert overview['campaigns'][0]['recipient_count'] == 2
    assert overview['campaigns'][0]['click_rate'] == 100
    assert b'100.0%' in client.get('/analytics').data


def test_empty_analytics_uses_empty_state_without_fabrication(db_client):
    client, _ = db_client
    response = client.get('/analytics')
    assert b'Waiting for an audience' in response.data
    assert b'Engagement starts with your audience' in response.data
    assert b'0 clicked' not in response.data


@pytest.mark.parametrize('url', ['/campaigns/1/recipients','/campaigns/1/recipients/new','/campaigns/1/results','/campaigns/1/studio','/campaigns/1/email-preview'])
def test_campaign_pages_render_and_preserve_workflow(campaign,url):
    client, _ = campaign
    assert client.get(url).status_code == 200


@pytest.mark.parametrize('layout',['standard','sectioned','compact'])
def test_safe_email_layouts_are_persisted_and_preview_matches_delivery(campaign,monkeypatch,layout):
    client, factory = campaign
    token = csrf(client)
    form = template_form() | {'body':'## Reminder\nHello {{recipient_name}}\n---\nReview the campaign.', 'button_label':'View awareness details','email_heading':'Make informed decisions','text_alignment':'center','content_style':layout,'csrf_token':token}
    assert post(client, '/templates/new',data=form).status_code == 302
    saved = template_store.get_template(1)
    assert saved['presentation']['button_label'] == 'View awareness details'
    assert post(client, '/campaigns/1/studio',data={'template_id':1,'csrf_token':token,'authorized':'on'}).status_code == 302
    snapshot = template_store.campaign_template(1)
    assert snapshot['presentation'] == saved['presentation']
    post(client, '/campaigns/1/launch')
    preview = client.get('/campaigns/1/email-preview')
    assert b'View awareness details' in preview.data and b'Make informed decisions' in preview.data
    FakeSMTP.messages=[];FakeSMTP.events=[]
    monkeypatch.setattr(email_service.smtplib,'SMTP',FakeSMTP)
    assert post(client, '/campaigns/1/recipients/1/send-email',data={'csrf_token':token,'authorized':'on'}).status_code == 302
    connection=factory()
    with connection.cursor() as cursor:
        cursor.execute('SELECT * FROM recipients WHERE id=1'); recipient=cursor.fetchone()
        cursor.execute('SELECT * FROM campaigns WHERE id=1'); campaign_row=cursor.fetchone()
    connection.close()
    expected=render_email(snapshot,campaign_row,recipient,email_service.build_tracking_url(recipient['tracking_token']))
    delivered=FakeSMTP.messages[0].get_body(preferencelist=('html',)).get_content().strip()
    assert delivered == expected['html']
    assert 'text-align:center' in delivered
    assert ('<h2' in delivered) == (layout=='sectioned')
    assert ('<hr' in delivered) == (layout=='sectioned')


@pytest.mark.parametrize('settings',[{'button_label':'x\nInjected'},{'button_label':''},{'text_alignment':'left; background:url(https://evil.example)'},{'content_style':'raw-html'},{'email_heading':'<script>unsafe</script>'},{'presentation':['invalid']}])
def test_unsafe_visual_settings_rejected(settings):
    with pytest.raises(TemplateValidationError):validate_template(template_form()|settings)


def test_visual_content_escaped_and_tracking_destination_fixed(monkeypatch):
    monkeypatch.setenv("SMTP_FROM_EMAIL","awareness@example.com")
    form=template_form() | {'body':'## {{recipient_name}}\n---\nA safe section','content_style':'sectioned','button_label':'Read <details>'}
    result=render_email(validate_template(form),{'campaign_name':'Awareness'},{'full_name':'<script>unsafe</script>'},'https://safe.example/t/token')
    assert '<script>' not in result['html'] and '&lt;script&gt;' in result['html']
    assert 'href="https://safe.example/t/token"' in result['html']
    assert 'Read &lt;details&gt;' in result['html']


def test_original_snapshot_without_presentation_keeps_readable_defaults():
    template=template_form()
    result=render_email(template,{'campaign_name':'Legacy'},{'full_name':'Alex'},'https://safe.example/t/legacy')
    assert 'Review information' in result['html']
    assert 'Hello Alex' in result['text']


def test_template_library_exposes_real_asset_metadata(db_client):
    client, _=db_client
    template_store.save_template(validate_template(template_form()),[])
    response=client.get('/templates')
    assert b'0 protected files' in response.data
    assert b'awareness@example.com' in response.data


def test_unknown_route_and_invalid_action_show_product_error_page(db_client):
    client, _ = db_client
    assert client.get('/not-a-page').status_code == 404
    response=post(client, '/campaigns/new',data={})
    assert response.status_code == 400
    assert b'role="alert"' in response.data and b'Missing required campaign information' in response.data


def test_invalid_public_link_has_no_admin_navigation_or_collection(db_client):
    client, _=db_client
    response=client.get('/t/invalid-link')
    assert response.status_code == 404
    assert b'Main navigation' not in response.data
    assert b'<input' not in response.data and b'<form' not in response.data


def test_presentation_changes_require_new_recipient_review(campaign,monkeypatch):
    client, _=campaign
    token=csrf(client)
    template_store.save_template(validate_template(template_form()),[])
    template_store.apply_template(1,template_store.get_template(1))
    post(client, '/campaigns/1/launch');client.get('/campaigns/1/email-preview')
    template_store.save_template(validate_template(template_form()|{'button_label':'Updated button'}),[])
    template_store.apply_template(1,template_store.get_template(2))
    FakeSMTP.messages=[];FakeSMTP.events=[]
    monkeypatch.setattr(email_service.smtplib,'SMTP',FakeSMTP)
    response=post(client, '/campaigns/1/recipients/1/send-email',data={'csrf_token':token,'authorized':'on'})
    assert response.status_code == 400 and not FakeSMTP.messages


def test_http_error_preserves_allowed_methods_header(db_client):
    client, _=db_client
    response=post(client, '/t/unavailable',data={})
    assert response.status_code == 405
    assert 'GET' in response.headers['Allow']


def test_presentation_migration_supports_original_templates(db_client):
    import database
    from sqlite_adapter import SQLiteConnection
    client,factory=db_client
    template_store.save_template(validate_template(template_form()),[])
    connection=factory()
    with connection.cursor() as cursor:cursor.execute('ALTER TABLE email_templates DROP COLUMN presentation')
    sqlite=isinstance(connection,SQLiteConnection)
    connection.close()
    if sqlite:
        connection=factory()
        with connection.cursor() as cursor:cursor.execute('ALTER TABLE email_templates ADD COLUMN presentation TEXT NULL')
        connection.close()
    else:
        database.initialize_database()
    template=template_store.get_template(1)
    assert template['presentation'] == {}
    assert 'Review information' in render_email(template,{'campaign_name':'Legacy'},{'full_name':'Alex'},'https://safe.example/t/legacy')['html']
