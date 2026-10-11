"""Constrained text templates, shared by the browser preview and SMTP delivery."""
import hashlib
import json
import os
import re
from html import escape
from urllib.parse import urlsplit

PLACEHOLDERS = {'recipient_name', 'tracking_url', 'campaign_name'}
TOKEN = re.compile(r'{{\s*([a-z_]+)\s*}}')
NOTICE = 'This campaign does not request passwords, OTPs, or authentication credentials.'
DEFAULT_TEMPLATE = {
    'name': 'Basic Link Awareness',
    'subject': 'Security Awareness: Review Required Information',
    'body': 'Hello {{recipient_name}},\n\nYou have been included in the authorized security awareness campaign:\n{{campaign_name}}\n\nPlease review the information using the link below:\n{{tracking_url}}',
    'sender_name': 'Security Awareness Team', 'sender_email': '', 'reference_link': '', 'assets': [],
}


class TemplateValidationError(ValueError):
    pass


def single_line(value, label, limit=200):
    if not value or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise TemplateValidationError(f'{label} must be a single line of 1–{limit} characters.')
    return value


def email_address(value):
    single_line(value, 'Email address', 254)
    if not re.fullmatch(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}', value):
        raise TemplateValidationError('Enter a valid email address.')
    return value


def sender_addresses():
    """Only operator configured identities; provider must authorize these aliases."""
    return list(dict.fromkeys(value.strip() for value in (
        os.getenv('SMTP_FROM_EMAIL', '') + ',' + os.getenv('SMTP_ALLOWED_FROM_EMAILS', '')
    ).split(',') if value.strip()))


def https_link(value):
    if not value:
        return ''
    if len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise TemplateValidationError('Reference link must be an HTTPS URL.')
    try:
        url = urlsplit(value)
        valid = url.scheme == 'https' and url.hostname and not url.username and not url.password
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise TemplateValidationError('Reference link must be an HTTPS URL without embedded credentials.')
    return value


def validate_placeholders(value):
    for match in TOKEN.finditer(value):
        if match[1] not in PLACEHOLDERS:
            raise TemplateValidationError(f'Unsupported placeholder: {match[1]}.')
    remaining = TOKEN.sub('', value)
    if '{{' in remaining or '}}' in remaining:
        raise TemplateValidationError('Malformed placeholder. Use {{recipient_name}}, {{tracking_url}}, or {{campaign_name}}.')


def validate_template(values):
    result = {key: str(values.get(key, '')).strip() for key in (
        'name', 'subject', 'body', 'sender_name', 'sender_email', 'reference_link'
    )}
    for key in ('name', 'subject', 'sender_name'):
        single_line(result[key], key.replace('_', ' ').title())
    body = result['body']
    if not body or len(body) > 20000 or any(ord(c) < 32 and c not in '\n\r\t' for c in body):
        raise TemplateValidationError('Body must contain 1–20,000 text characters.')
    # HTML is never interpreted: no forms, scripts, or arbitrary markup in emails.
    if re.search(r'<\s*/?\s*[a-z!]', body, re.I):
        raise TemplateValidationError('Use plain text, without HTML or credential collection forms.')
    for key in ('subject', 'body'):
        validate_placeholders(result[key])
    email_address(result['sender_email'])
    if result['sender_email'] not in sender_addresses():
        raise TemplateValidationError('Sender must be an operator-configured authenticated SMTP identity.')
    result['reference_link'] = https_link(result['reference_link'])
    return result


def render_email(template, campaign, recipient, tracking_url, image_source=None):
    context = {'recipient_name': recipient['full_name'], 'campaign_name': campaign['campaign_name'], 'tracking_url': tracking_url}
    def substitute(text):
        validate_placeholders(text)
        return TOKEN.sub(lambda match: str(context[match[1]]), text)
    subject = single_line(substitute(template['subject']), 'Rendered subject', 998)
    body = substitute(template['body'])
    text = body + '\n\n'
    # Always retain a campaign link even if an author leaves out its placeholder.
    if tracking_url not in body:
        text += 'Review information: ' + tracking_url + '\n\n'
    reference = https_link(template.get('reference_link', ''))
    if reference:
        text += 'Reference: ' + reference + '\n\n'
    text += NOTICE
    html = '<div style="font-family:Arial,sans-serif;max-width:640px;margin:auto;padding:28px;color:#172033">'
    if image_source:
        html += f'<img src="{escape(image_source, quote=True)}" alt="Campaign illustration" style="max-width:100%;height:auto">'
    html += '<div style="white-space:pre-wrap;line-height:1.7">' + escape(body) + '</div>'
    html += f'<p><a href="{escape(tracking_url, quote=True)}">Review information</a></p>'
    if reference:
        html += f'<p>Reference: <a href="{escape(reference, quote=True)}">{escape(reference)}</a></p>'
    html += '<p style="font-size:12px;color:#64748b">' + NOTICE + '</p></div>'
    return {'subject': subject, 'text': text, 'html': html,
            'sender_name': template['sender_name'], 'sender_email': template['sender_email']}


def preview_digest(template, campaign, recipient, tracking_url):
    """Bind review to exact content, assets, recipient, URL, and sender configuration."""
    payload = {'template': template, 'campaign': campaign['campaign_name'],
               'recipient': [recipient['id'], recipient['full_name'], recipient['email']],
               'tracking_url': tracking_url, 'senders': sender_addresses()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
