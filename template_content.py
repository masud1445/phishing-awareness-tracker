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
    account_id = values.get('sender_account_id')
    result['sender_account_id'] = None
    if account_id:
        from sender_accounts import require_verified
        try:
            account_id = int(account_id)
        except (ValueError, TypeError):
            raise TemplateValidationError('Choose a saved sender account.') from None
        account = require_verified(account_id)
        if result['sender_email'] and result['sender_email'] != account['email']:
            raise TemplateValidationError('Sender address does not match the selected verified account.')
        result['sender_email'] = account['email']
        result['sender_account_id'] = account_id
    email_address(result['sender_email'])
    if not account_id and result['sender_email'] not in sender_addresses():
        raise TemplateValidationError('Sender must be an operator-configured authenticated SMTP identity.')
    result['reference_link'] = https_link(result['reference_link'])
    result['presentation'] = validate_presentation(values)
    return result


def validate_presentation(values):
    raw = values.get('presentation') or values
    if not isinstance(raw, dict) and not hasattr(raw, 'get'):
        raise TemplateValidationError('Invalid email presentation settings.')
    result = {'email_heading': str(raw.get('email_heading', '')).strip(),
              'button_label': str(raw.get('button_label', 'Review information')).strip(),
              'text_alignment': str(raw.get('text_alignment', 'left')),
              'content_style': str(raw.get('content_style', 'standard'))}
    single_line(result['button_label'], 'Tracking button label', 80)
    if result['email_heading']:
        single_line(result['email_heading'], 'Email heading', 200)
        if '<' in result['email_heading'] or '>' in result['email_heading']:
            raise TemplateValidationError('Email headings must use plain text.')
    if result['text_alignment'] not in ('left', 'center') or result['content_style'] not in ('standard', 'sectioned', 'compact'):
        raise TemplateValidationError('Choose a supported email alignment and layout.')
    return result


def render_email(template, campaign, recipient, tracking_url, image_source=None):
    context = {'recipient_name': recipient['full_name'], 'campaign_name': campaign['campaign_name'], 'tracking_url': tracking_url}
    def substitute(text):
        validate_placeholders(text)
        return TOKEN.sub(lambda match: str(context[match[1]]), text)
    subject = single_line(substitute(template['subject']), 'Rendered subject', 998)
    body = substitute(template['body'])
    presentation = validate_presentation(template)
    heading = presentation['email_heading']
    text = (heading + '\n\n' if heading else '') + body + '\n\n'
    # Always retain a campaign link even if an author leaves out its placeholder.
    if tracking_url not in body:
        text += presentation['button_label'] + ': ' + tracking_url + '\n\n'
    reference = https_link(template.get('reference_link', ''))
    if reference:
        text += 'Reference: ' + reference + '\n\n'
    text += NOTICE
    alignment = presentation['text_alignment']
    padding = 20 if presentation['content_style'] == 'compact' else 32
    html = f'<div style="font-family:Arial,sans-serif;max-width:640px;margin:auto;padding:{padding}px;color:#172033;text-align:{alignment};background:#ffffff;border-top:4px solid #635be7">'
    if heading:
        html += '<h1 style="font-size:26px;line-height:1.3;margin:0 0 24px">' + escape(heading) + '</h1>'
    if image_source:
        html += f'<img src="{escape(image_source, quote=True)}" alt="Campaign illustration" style="max-width:100%;height:auto">'
    if presentation['content_style'] == 'sectioned':
        for line in body.split('\n'):
            if line.startswith('## '):
                html += '<h2 style="font-size:20px;margin:24px 0 12px">' + escape(line[3:]) + '</h2>'
            elif line.strip() == '---':
                html += '<hr style="border:0;border-top:1px solid #dfe4f0;margin:24px 0">'
            else:
                html += '<div style="white-space:pre-wrap;line-height:1.7;min-height:12px">' + escape(line) + '</div>'
    else:
        html += '<div style="white-space:pre-wrap;line-height:1.7">' + escape(body) + '</div>'
    html += f'<p style="margin:24px 0"><a style="display:inline-block;padding:12px 20px;background:#4f46e5;color:#ffffff;border-radius:8px;text-decoration:none;font-weight:bold" href="{escape(tracking_url, quote=True)}">' + escape(presentation['button_label']) + '</a></p>'
    if reference:
        html += f'<p>Reference: <a href="{escape(reference, quote=True)}">{escape(reference)}</a></p>'
    html += '<p style="font-size:12px;color:#64748b">' + NOTICE + '</p></div>'
    return {'subject': subject, 'text': text, 'html': html,
            'sender_name': template['sender_name'], 'sender_email': template['sender_email']}


def preview_digest(template, campaign, recipient, tracking_url):
    """Bind review to exact content, assets, recipient, URL, and sender configuration."""
    payload = {'template': template, 'campaign': campaign['campaign_name'],
               'recipient': [recipient['id'], recipient['full_name'], recipient['email']],
               'tracking_url': tracking_url, 'senders': sender_addresses(), 'renderer_version': 2}
    if template.get('sender_account_id'):
        from sender_accounts import require_verified
        account = require_verified(template['sender_account_id'], template['sender_email'])
        payload['sender_account_revision'] = account['revision']
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
