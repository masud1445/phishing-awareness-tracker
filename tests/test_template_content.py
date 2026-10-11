import pytest

from conftest import template_form
from template_content import TemplateValidationError, render_email, validate_template


@pytest.fixture(autouse=True)
def identities(monkeypatch):
    monkeypatch.setenv('SMTP_FROM_EMAIL', 'awareness@example.com')
    monkeypatch.setenv('SMTP_ALLOWED_FROM_EMAILS', 'training@example.com')


def test_all_placeholders_and_html_escaping():
    template = validate_template(template_form())
    result = render_email(template, {'campaign_name': 'Quarterly <test>'}, {'full_name': '<script>Alex</script>'}, 'https://example.com/t/abc')
    assert result['subject'] == 'Review Quarterly <test>'
    assert 'Hello <script>Alex</script>' in result['text']
    assert '<script>' not in result['html']
    assert '&lt;script&gt;' in result['html']
    assert 'https://example.com/t/abc' in result['html']
    assert 'Reference:' in result['text']
    assert 'passwords, OTPs' in result['text']


@pytest.mark.parametrize('field,value', [
    ('subject', 'Hello\r\nBcc: victim@example.com'), ('sender_name', 'Team\nInjected'),
    ('sender_email', 'spoof@example.net'), ('sender_email', 'bad address'),
    ('body', '<form><input type="password"></form>'), ('body', '<script>alert(1)</script>'),
    ('body', '{{password}}'), ('body', '{{recipient_name'), ('subject', '{{unknown}}'),
    ('reference_link', 'javascript:alert(1)'), ('reference_link', 'http://example.com'),
    ('reference_link', 'https://user:password@example.com'), ('reference_link', 'https://example.com\n'),
    ('body', ''), ('name', ''), ('body', 'x' * 20001),
])
def test_invalid_templates_rejected(field, value):
    values = template_form() | {field: value}
    # Validation strips outer whitespace; embedded URL controls are rejected.
    if field == 'reference_link' and value.endswith('\n'):
        values[field] = 'https://exa\nmple.com'
    with pytest.raises(TemplateValidationError):
        validate_template(values)


def test_configured_alias_and_optional_reference():
    result = validate_template(template_form() | {'sender_email': 'training@example.com', 'reference_link': ''})
    rendered = render_email(result, {'campaign_name': 'Test'}, {'full_name': 'Alex'}, 'https://example.com/t/a')
    assert 'Reference:' not in rendered['text']


def test_missing_tracking_placeholder_still_includes_link():
    template = validate_template(template_form() | {'body': 'Hello {{ recipient_name }}'})
    rendered = render_email(template, {'campaign_name': 'Test'}, {'full_name': 'Alex'}, 'https://example.com/t/a')
    assert 'https://example.com/t/a' in rendered['text']
    assert 'https://example.com/t/a' in rendered['html']


def test_substitution_is_not_recursive():
    template = validate_template(template_form())
    rendered = render_email(template, {'campaign_name': '{{recipient_name}}'}, {'full_name': 'Alex'}, 'https://example.com/t/a')
    assert rendered['subject'] == 'Review {{recipient_name}}'


def test_rendered_header_injection_blocked():
    with pytest.raises(TemplateValidationError):
        render_email(validate_template(template_form()), {'campaign_name': 'Test\nBcc: other@example.com'}, {'full_name': 'Alex'}, 'https://example.com/t/a')
