import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlsplit

from dotenv import load_dotenv

from template_content import (DEFAULT_TEMPLATE, TemplateValidationError, email_address,
                              render_email, sender_addresses, single_line, validate_template)
from template_store import get_asset

load_dotenv()


class EmailDeliveryError(Exception):
    pass


def build_tracking_url(tracking_token):
    base_url = os.getenv('BASE_URL', 'http://127.0.0.1:5055').rstrip('/')
    url = urlsplit(base_url)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
        raise EmailDeliveryError('BASE_URL must be an HTTP or HTTPS URL without credentials.')
    if not tracking_token or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in tracking_token):
        raise EmailDeliveryError('Invalid tracking token.')
    return f'{base_url}/t/{tracking_token}'


def effective_template(campaign):
    if campaign.get('email_template'):
        return campaign['email_template']
    return DEFAULT_TEMPLATE | {'sender_name': os.getenv('SMTP_FROM_NAME', 'Security Awareness Team'),
                               'sender_email': os.getenv('SMTP_FROM_EMAIL', '')}


def build_campaign_message(campaign, recipient):
    template = effective_template(campaign)
    try:
        if campaign.get('email_template'):
            validate_template(template)
        email_address(recipient['email'])
        email_address(template['sender_email'])
        single_line(template['sender_name'], 'Sender name')
        if not template.get('sender_account_id') and template['sender_email'] not in sender_addresses():
            raise TemplateValidationError('Sender is not configured for authenticated delivery.')
        assets = template.get('assets', [])
        illustration = next((asset for asset in assets if asset['kind'] == 'image'), None)
        rendered = render_email(template, campaign, recipient, build_tracking_url(recipient['tracking_token']),
                                'cid:campaign-illustration' if illustration else None)
        message = EmailMessage()
        message['Subject'] = rendered['subject']
        message['From'] = formataddr((rendered['sender_name'], rendered['sender_email']))
        message['To'] = recipient['email']
        message.set_content(rendered['text'])
        message.add_alternative(rendered['html'], subtype='html')
        for metadata in assets:
            asset = get_asset(metadata['id'])
            if not asset:
                raise TemplateValidationError('A campaign asset is unavailable.')
            import hashlib
            if hashlib.sha256(asset['data']).hexdigest() != metadata['sha256']:
                raise TemplateValidationError('A campaign asset has changed. Review a new template.')
            maintype, subtype = metadata['mime_type'].split('/', 1)
            if metadata['kind'] == 'image':
                message.get_payload()[1].add_related(asset['data'], maintype=maintype, subtype=subtype,
                                                    cid='<campaign-illustration>', filename=metadata['filename'], disposition='inline')
            else:
                message.add_attachment(asset['data'], maintype=maintype, subtype=subtype, filename=metadata['filename'])
        return message
    except (TemplateValidationError, ValueError) as exc:
        raise EmailDeliveryError(str(exc)) from exc


def send_campaign_email(campaign, recipient):
    if campaign.get('status') != 'active':
        raise EmailDeliveryError('Campaign must be active before email delivery.')
    if not recipient.get('approved') or recipient.get('campaign_id') != campaign.get('id'):
        raise EmailDeliveryError('Recipient is not approved for this campaign.')
    if not campaign.get('authorized'):
        raise EmailDeliveryError('Campaign authorization confirmation is required.')
    template = effective_template(campaign)
    if template.get('sender_account_id'):
        from sender_accounts import delivery_account, open_account_connection
        try:
            with delivery_account(template['sender_account_id'], template['sender_email'], campaign.get('_delivery_connection')) as settings:
                message = build_campaign_message(campaign, recipient)
                with open_account_connection(settings) as server:
                    server.send_message(message, from_addr=template['sender_email'], to_addrs=[recipient['email']])
        except (TemplateValidationError, smtplib.SMTPException, OSError, ValueError):
            raise EmailDeliveryError('Verified sender delivery failed. Check account status and Test connection again.') from None
        return True
    required = {key: os.getenv(key) for key in ('SMTP_HOST', 'SMTP_USER', 'SMTP_PASSWORD', 'SMTP_FROM_EMAIL')}
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise EmailDeliveryError('SMTP configuration missing: ' + ', '.join(missing))
    try:
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        message = build_campaign_message(campaign, recipient)
        with smtplib.SMTP(required['SMTP_HOST'], smtp_port, timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(required['SMTP_USER'], required['SMTP_PASSWORD'])
            server.send_message(message)
    except EmailDeliveryError:
        raise
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        # Do not expose provider responses, credentials, or transport details to the browser.
        raise EmailDeliveryError('Authenticated SMTP delivery failed. Check server configuration and provider logs.') from exc
    return True
