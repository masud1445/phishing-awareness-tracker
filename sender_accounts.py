"""Encrypted SMTP accounts. Public projections never contain a credential."""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import smtplib
import socket
import ssl
from contextlib import contextmanager

from cryptography.fernet import Fernet, InvalidToken

from database import get_db_connection
from template_content import TemplateValidationError, email_address, single_line, sender_addresses

PUBLIC_FIELDS = ('id', 'display_name', 'email', 'provider', 'smtp_host', 'smtp_port', 'tls_mode',
                 'smtp_username', 'enabled', 'deleted', 'verified', 'revision', 'source_key')
PROVIDERS = {'gmail': ('smtp.gmail.com', 587, 'starttls'),
             'outlook': ('smtp.office365.com', 587, 'starttls'), 'custom': None}


class SenderAccountError(TemplateValidationError):
    pass


def cipher():
    try:
        return Fernet(os.environ['SENDER_ENCRYPTION_KEY'].encode())
    except (KeyError, ValueError, TypeError):
        raise SenderAccountError('Configure a valid SENDER_ENCRYPTION_KEY in server environment settings.') from None


def public_account(row):
    return {name: row[name] for name in PUBLIC_FIELDS}


def get_account(account_id, private=False, connection=None):
    owned = connection is None
    connection = connection or get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM sender_accounts WHERE id=%s', (account_id,))
            row = cursor.fetchone()
            return row if private or row is None else public_account(row)
    finally:
        if owned:
            connection.close()


def list_accounts(include_deleted=False):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM sender_accounts' + ('' if include_deleted else ' WHERE deleted=0') + ' ORDER BY CASE WHEN source_key IS NULL THEN 1 ELSE 0 END, id')
            rows = [public_account(row) for row in cursor.fetchall()]
            cursor.execute('SELECT default_sender_id FROM sender_preferences WHERE id=1')
            preference = cursor.fetchone()
            default = preference['default_sender_id'] if preference else None
            return [row | {'is_default': row['id'] == default} for row in rows]
    finally:
        connection.close()


def synchronize_configured():
    """Expose pre-existing configuration without copying its password into SQL."""
    connection = get_db_connection()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            for email in sender_addresses():
                key = 'configured:' + email
                cursor.execute('SELECT id FROM sender_accounts WHERE source_key=%s', (key,))
                if cursor.fetchone():
                    continue
                cursor.execute('INSERT INTO sender_accounts (display_name,email,provider,smtp_host,smtp_port,tls_mode,smtp_username,source_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                               (os.getenv('SMTP_FROM_NAME', 'Security Awareness Team'), email, 'custom',
                                os.getenv('SMTP_HOST', ''), int(os.getenv('SMTP_PORT', '587')),
                                os.getenv('SMTP_TLS_MODE', 'starttls'), os.getenv('SMTP_USER', ''), key))
            cursor.execute('SELECT id FROM sender_preferences WHERE id=1')
            if not cursor.fetchone():
                cursor.execute('INSERT INTO sender_preferences (id,default_sender_id) VALUES (1,NULL)')
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def validate_account(values):
    result = {name: str(values.get(name, '')).strip() for name in
              ('display_name', 'email', 'provider', 'smtp_host', 'tls_mode', 'smtp_username')}
    single_line(result['display_name'], 'Display name')
    email_address(result['email'])
    single_line(result['smtp_username'], 'SMTP username', 254)
    if result['provider'] not in PROVIDERS:
        raise SenderAccountError('Choose Gmail, Microsoft/Outlook, or Custom SMTP.')
    preset = PROVIDERS[result['provider']]
    if preset:
        # Users cannot smuggle a different destination beneath a trusted provider label.
        result['smtp_host'] = preset[0]
    host = result['smtp_host'].lower()
    if len(host) > 253 or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host):
        raise SenderAccountError('SMTP host must be a DNS hostname without a URL, path, or port.')
    result['smtp_host'] = host
    try:
        result['smtp_port'] = int(values.get('smtp_port', 587))
    except (ValueError, TypeError):
        raise SenderAccountError('SMTP port must be between 1 and 65535.') from None
    if not 1 <= result['smtp_port'] <= 65535 or result['tls_mode'] not in ('starttls', 'ssl'):
        raise SenderAccountError('Use a valid SMTP port and STARTTLS or SSL/TLS; plaintext SMTP is not supported.')
    if preset and (result['smtp_port'], result['tls_mode']) not in ((587, 'starttls'), (465, 'ssl')):
        raise SenderAccountError('Provider accounts require port 587 with STARTTLS or port 465 with SSL/TLS.')
    if result['provider'] == 'outlook' and (result['smtp_port'], result['tls_mode']) != (587, 'starttls'):
        raise SenderAccountError('Microsoft/Outlook requires STARTTLS on port 587.')
    return result


def save_account(values, password, account_id=None):
    result = validate_account(values)
    password = password or ''
    if len(password) > 4096 or '\x00' in password:
        raise SenderAccountError('Invalid SMTP/app password.')
    encryption = cipher()  # Missing/invalid key always fails closed, even on blank edits.
    encrypted = encryption.encrypt(password.encode()).decode() if password else None
    connection = get_db_connection()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            if account_id:
                cursor.execute('SELECT * FROM sender_accounts WHERE id=%s FOR UPDATE', (account_id,))
                old = cursor.fetchone()
                if not old or old['deleted']:
                    raise SenderAccountError('Sender account not found.')
                if old['source_key']:
                    raise SenderAccountError('The existing configured sender is edited through server environment settings.')
                encrypted = encrypted or old['encrypted_password']
                cursor.execute('UPDATE sender_accounts SET display_name=%s,email=%s,provider=%s,smtp_host=%s,smtp_port=%s,tls_mode=%s,smtp_username=%s,encrypted_password=%s,verified=0,verification_digest=NULL,revision=revision+1 WHERE id=%s',
                               tuple(result[name] for name in ('display_name','email','provider','smtp_host','smtp_port','tls_mode','smtp_username')) + (encrypted, account_id))
                cursor.execute('UPDATE sender_preferences SET default_sender_id=NULL WHERE default_sender_id=%s', (account_id,))
            else:
                if not encrypted:
                    raise SenderAccountError('An SMTP/app password is required for a new account.')
                cursor.execute('INSERT INTO sender_accounts (display_name,email,provider,smtp_host,smtp_port,tls_mode,smtp_username,encrypted_password) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                               tuple(result[name] for name in ('display_name','email','provider','smtp_host','smtp_port','tls_mode','smtp_username')) + (encrypted,))
                account_id = cursor.lastrowid
        connection.commit()
        return account_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def connection_settings(row):
    if row['source_key']:
        if row['email'] not in sender_addresses():
            raise SenderAccountError('Configured sender is no longer available.')
        password = os.getenv('SMTP_PASSWORD', '')
        settings = row | {'smtp_host': os.getenv('SMTP_HOST', ''), 'smtp_port': os.getenv('SMTP_PORT', '587'),
                          'tls_mode': os.getenv('SMTP_TLS_MODE', 'starttls'), 'smtp_username': os.getenv('SMTP_USER', '')}
        settings = validate_account(settings)
    else:
        settings = validate_account(row)
        try:
            password = cipher().decrypt(row['encrypted_password'].encode()).decode()
        except (InvalidToken, AttributeError, UnicodeError):
            raise SenderAccountError('Sender secret cannot be decrypted. Restore the original encryption key or re-enter the account password.') from None
    if not password:
        raise SenderAccountError('Configure the SMTP/app password on the server.')
    return settings | {'password': password}


def settings_digest(settings):
    # The verification marker is keyed; it is not an offline password hash.
    key = os.environ.get('SENDER_ENCRYPTION_KEY', '').encode()
    cipher()
    return hmac.new(key, json.dumps(settings, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def require_verified(account_id, email=None, connection=None):
    row = get_account(account_id, private=True, connection=connection)
    if not row or row['deleted'] or not row['enabled'] or not row['verified']:
        raise SenderAccountError('Choose an enabled, verified sender account. Use Test connection first.')
    if email is not None and row['email'] != email:
        raise SenderAccountError('Sender identity changed. Select the account again and review the campaign.')
    settings = connection_settings(row)
    if not hmac.compare_digest(row['verification_digest'] or '', settings_digest(settings)):
        raise SenderAccountError('Sender settings changed. Test connection again before delivery.')
    return public_account(row)


def safe_destination(host, port):
    """Reject local/private destinations before transmitting an operator secret."""
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
            raise SenderAccountError('SMTP destination must resolve only to public addresses.')
    except OSError:
        raise SenderAccountError('SMTP hostname could not be resolved.') from None
    # Connect to a checked address; retain the DNS name for certificate verification.
    return addresses[0][4][0]


class PinnedSMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        return socket.create_connection((self.checked_address, port), timeout, self.source_address)


class PinnedSMTPSSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        sock = socket.create_connection((self.checked_address, port), timeout, self.source_address)
        return self.context.wrap_socket(sock, server_hostname=host)


@contextmanager
def open_account_connection(settings):
    address = safe_destination(settings['smtp_host'], settings['smtp_port'])
    context = ssl.create_default_context()
    cls = PinnedSMTPSSL if settings['tls_mode'] == 'ssl' else PinnedSMTP
    # Delay connect until the pinned address is assigned.
    server = cls(timeout=20, context=context) if settings['tls_mode'] == 'ssl' else cls(timeout=20)
    server.checked_address = address
    server._host = settings['smtp_host']  # STARTTLS validates this original DNS name.
    try:
        server.connect(settings['smtp_host'], settings['smtp_port'])
        if settings['tls_mode'] == 'starttls':
            server.starttls(context=context)
        server.login(settings['smtp_username'], settings['password'])
        yield server
    finally:
        try:
            server.quit()
        except (OSError, smtplib.SMTPException):
            server.close()


def verify_account(account_id):
    connection = get_db_connection()
    try:
        connection.begin()
        row = lock_account(account_id, connection)
        if row['deleted'] or not row['enabled']:
            raise SenderAccountError('Sender account is disabled or deleted.')
        with connection.cursor() as cursor:
            cursor.execute('UPDATE sender_accounts SET verified=0,verification_digest=NULL,revision=revision+1 WHERE id=%s', (account_id,))
            cursor.execute('UPDATE sender_preferences SET default_sender_id=NULL WHERE default_sender_id=%s', (account_id,))
        connection.commit()
        row['revision'] += 1
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    settings = connection_settings(row)
    try:
        with open_account_connection(settings) as server:
            # MAIL FROM / RSET checks sender acceptance, but never submits a recipient or message.
            code, _ = server.mail(row['email'])
            server.rset()
            if code != 250:
                raise SenderAccountError('SMTP server did not accept this sender identity.')
    except SenderAccountError:
        raise
    except (OSError, smtplib.SMTPException, ValueError):
        raise SenderAccountError('Connection verification failed. Check provider settings, SMTP access, and app password.') from None
    connection = get_db_connection()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute('SELECT revision, enabled, deleted FROM sender_accounts WHERE id=%s FOR UPDATE', (account_id,))
            current = cursor.fetchone()
            if not current or current['revision'] != row['revision'] or not current['enabled'] or current['deleted']:
                raise SenderAccountError('Account changed during verification. Test again.')
            cursor.execute('UPDATE sender_accounts SET verified=1,verification_digest=%s,revision=revision+1 WHERE id=%s', (settings_digest(settings), account_id))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def account_action(account_id, action):
    if action not in ('default', 'disable', 'enable', 'delete'):
        raise SenderAccountError('Unknown sender action.')
    connection = get_db_connection()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM sender_accounts WHERE id=%s FOR UPDATE', (account_id,))
            row = cursor.fetchone()
            if not row or row['deleted']:
                raise SenderAccountError('Sender account not found.')
            if action == 'default':
                require_verified(account_id, connection=connection)
                cursor.execute('UPDATE sender_preferences SET default_sender_id=%s WHERE id=1', (account_id,))
            else:
                if action == 'delete':
                    cursor.execute('UPDATE sender_accounts SET deleted=1,enabled=0,verified=0,encrypted_password=NULL,verification_digest=NULL,revision=revision+1 WHERE id=%s', (account_id,))
                else:
                    cursor.execute('UPDATE sender_accounts SET enabled=%s,verified=0,verification_digest=NULL,revision=revision+1 WHERE id=%s', (int(action == 'enable'), account_id))
                cursor.execute('UPDATE sender_preferences SET default_sender_id=NULL WHERE default_sender_id=%s', (account_id,))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def selectable_accounts():
    accounts = []
    for row in list_accounts():
        try:
            require_verified(row['id'])
            accounts.append(row)
        except TemplateValidationError:
            pass
    return accounts


def lock_account(account_id, connection):
    with connection.cursor() as cursor:
        cursor.execute('SELECT * FROM sender_accounts WHERE id=%s FOR UPDATE', (account_id,))
        row = cursor.fetchone()
    if row is None:
        raise SenderAccountError('Sender account not found.')
    return row


@contextmanager
def delivery_account(account_id, email, connection=None):
    owned = connection is None
    connection = connection or get_db_connection()
    try:
        if owned:
            connection.begin()
        row = lock_account(account_id, connection)
        require_verified(account_id, email, connection)
        yield connection_settings(row)
        if owned:
            connection.commit()
    except Exception:
        if owned:
            connection.rollback()
        raise
    finally:
        if owned:
            connection.close()
