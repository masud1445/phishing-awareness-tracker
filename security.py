"""Single-operator session authentication and fail-closed administrative CSRF."""
import hashlib
import hmac
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import timedelta

import click
from flask import Blueprint, abort, current_app, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

operator = Blueprint('operator', __name__)
LOGIN_WINDOW = 600
LOGIN_ATTEMPTS = 5


def csrf_token():
    if 'studio_csrf' not in session:
        session['studio_csrf'] = secrets.token_urlsafe(32)
    return session['studio_csrf']


def require_csrf():
    supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token', '')
    expected = session.get('studio_csrf', '')
    if not expected or not hmac.compare_digest(expected.encode(), supplied.encode()):
        abort(400, 'Invalid form token. Reload the page and try again.')


def credentials():
    username = current_app.config.get('ADMIN_USERNAME', '')
    encoded = current_app.config.get('ADMIN_PASSWORD_HASH', '')
    if not isinstance(username, str) or not username or len(username) > 200:
        return None
    if not isinstance(encoded, str):
        return None
    parts = encoded.split('$')
    if len(parts) != 3 or len(parts[1]) < 16 or not re.fullmatch(r'[0-9a-f]+', parts[2]):
        return None
    method = parts[0].split(':')
    try:
        strong = (len(method) == 4 and method[0] == 'scrypt' and int(method[1]) >= 32768
                  and int(method[2]) >= 8 and int(method[3]) >= 1 and len(parts[2]) == 128)
        strong = strong or (len(method) == 3 and method[:2] == ['pbkdf2', 'sha256']
                            and int(method[2]) >= 600000 and len(parts[2]) == 64)
    except ValueError:
        return None
    if not strong:
        return None
    if current_app.config.get('APP_ENV') == 'production' and not current_app.config.get('PRODUCTION_AUTH_READY'):
        return None
    return username, encoded


def auth_fingerprint(account):
    return hmac.new(current_app.secret_key.encode(), ('operator\0' + '\0'.join(account)).encode(), hashlib.sha256).hexdigest()


def operator_authenticated():
    account = credentials()
    stamp = session.get('operator_login_at')
    fingerprint = session.get('operator_auth', '')
    if not account or not isinstance(stamp, (float, int)) or not isinstance(fingerprint, str):
        return False
    nonce = session.get('operator_nonce', '')
    state = current_app.extensions['operator_sessions']
    with state['lock']:
        active = isinstance(nonce, str) and state['active'].get(hashlib.sha256(nonce.encode()).hexdigest()) == stamp
    age = time.time() - stamp
    return (active and 0 <= age < current_app.config['OPERATOR_SESSION_MINUTES'] * 60
            and hmac.compare_digest(fingerprint, auth_fingerprint(account)))


def login_budget():
    """Bounded per-process throttle; deployments also require gateway rate limits."""
    state = current_app.extensions['operator_login_throttle']
    now = time.monotonic()
    address = request.remote_addr or 'unknown'
    # Keep the lock only while updating counters, never while hashing a password.
    with state['lock']:
        for key in list(state['attempts']):
            history = state['attempts'][key]
            while history and now - history[0] >= LOGIN_WINDOW:
                history.popleft()
            if not history:
                del state['attempts'][key]
        history = state['attempts'].get(address)
        if (history is not None and len(history) >= LOGIN_ATTEMPTS) or (history is None and len(state['attempts']) >= 4096):
            return False
        state['attempts'].setdefault(address, deque()).append(now)
    return True


@operator.route('/operator/login', methods=['GET', 'POST'])
def login():
    account = credentials()
    if not account:
        return render_template('operator_login.html', error='Operator access is not configured. Contact the server administrator.', unavailable=True), 503
    if operator_authenticated():
        return redirect(url_for('home'))
    if request.method == 'POST':
        if not login_budget():
            response = current_app.make_response((render_template('operator_login.html', error='Too many sign-in attempts. Try again in ten minutes.', unavailable=False), 429))
            response.headers['Retry-After'] = str(LOGIN_WINDOW)
            return response
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        try:
            valid_password = len(password) <= 4096 and check_password_hash(account[1], password)
        except (ValueError, TypeError):
            valid_password = False
        if valid_password and hmac.compare_digest(username.encode(), account[0].encode()):
            session.clear()  # Drop pre-login CSRF/review state; issue a new authenticated session.
            session.permanent = True
            session['operator_auth'] = auth_fingerprint(account)
            stamp = time.time()
            nonce = secrets.token_urlsafe(32)
            state = current_app.extensions['operator_sessions']
            with state['lock']:
                state['active'] = {key: value for key, value in state['active'].items()
                                   if stamp - value < current_app.config['OPERATOR_SESSION_MINUTES'] * 60}
                if len(state['active']) >= 1024:
                    session.clear()
                    abort(503, 'Operator session capacity reached. Try again later.')
                state['active'][hashlib.sha256(nonce.encode()).hexdigest()] = stamp
            session['operator_login_at'] = stamp
            session['operator_nonce'] = nonce
            csrf_token()
            return redirect(url_for('home'))
        return render_template('operator_login.html', error='Invalid operator credentials.', unavailable=False), 401
    return render_template('operator_login.html', error=None, unavailable=False)


@operator.route('/operator/logout', methods=['POST'])
def logout():
    nonce = session.get('operator_nonce', '')
    state = current_app.extensions['operator_sessions']
    with state['lock']:
        state['active'].pop(hashlib.sha256(nonce.encode()).hexdigest(), None)
    session.clear()
    return redirect(url_for('operator.login'))


def configure_security(app):
    try:
        minutes = int(os.getenv('OPERATOR_SESSION_MINUTES', '30'))
    except ValueError:
        minutes = 30
    minutes = max(1, min(minutes, 480))
    production = os.getenv('APP_ENV', 'local') == 'production'
    hosts = [host.strip() for host in os.getenv('TRUSTED_HOSTS', '').split(',') if host.strip()]
    app.config.update(ADMIN_USERNAME=os.getenv('ADMIN_USERNAME', ''),
                      ADMIN_PASSWORD_HASH=os.getenv('ADMIN_PASSWORD_HASH', ''),
                      APP_ENV='production' if production else 'local',
                      PRODUCTION_AUTH_READY=bool(os.getenv('SECRET_KEY') and len(os.getenv('SECRET_KEY', '')) >= 32
                                                and app.config['SESSION_COOKIE_SECURE'] and hosts),
                      TRUSTED_HOSTS=hosts or ['localhost', '127.0.0.1', '[::1]'],
                      OPERATOR_SESSION_MINUTES=minutes, PERMANENT_SESSION_LIFETIME=timedelta(minutes=minutes),
                      SESSION_REFRESH_EACH_REQUEST=False)
    app.extensions['operator_sessions'] = {'lock': threading.Lock(), 'active': {}}
    app.extensions['operator_login_throttle'] = {'lock': threading.Lock(), 'attempts': {}}
    app.register_blueprint(operator)

    @app.before_request
    def administrative_access():
        # Endpoint allowlist, never a user-supplied path prefix or tracking token.
        if request.endpoint in ('static', 'track_recipient_click'):
            return None
        if request.endpoint is None:
            return None  # Routing errors have no mutation or private asset lookup.
        if request.content_length and request.content_length > app.config['MAX_CONTENT_LENGTH']:
            abort(413, 'Request too large. Each file is limited to 5 MB.')
        if request.endpoint != 'operator.login' and not operator_authenticated():
            session.clear()
            if request.endpoint == 'studio.asset_download' or request.method not in ('GET', 'HEAD'):
                abort(401, 'Operator sign-in is required.')
            return redirect(url_for('operator.login'))
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            require_csrf()

    @app.after_request
    def protect_private_responses(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('Referrer-Policy', 'no-referrer')
        # Admin pages cannot be embedded by another origin. Asset responses keep their sandbox.
        if request.endpoint not in ('static', 'track_recipient_click'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers.setdefault('Content-Security-Policy', "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        return response

    @app.cli.command('hash-operator-password')
    def hash_operator_password():
        """Generate a private operator password hash; never enter plaintext in shell arguments."""
        password = click.prompt('Operator password', hide_input=True, confirmation_prompt=True)
        if len(password) < 12 or len(password) > 4096:
            raise click.ClickException('Use an operator password of 12–4096 characters.')
        click.echo(generate_password_hash(password))
