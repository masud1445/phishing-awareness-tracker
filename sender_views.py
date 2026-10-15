"""Operator-only account management forms; passwords are write-only inputs."""
from template_content import TemplateValidationError
from flask import Blueprint, abort, redirect, render_template, request, url_for

from sender_accounts import (SenderAccountError, account_action, get_account, list_accounts,
                             save_account, synchronize_configured, verify_account)

senders = Blueprint('senders', __name__)


@senders.after_request
def private_responses(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'same-origin'
    return response


@senders.route('/senders')
def manage():
    synchronize_configured()
    return render_template('sender_accounts.html', accounts=list_accounts(), error=None)


@senders.route('/senders/new', methods=['GET', 'POST'])
@senders.route('/senders/<int:account_id>/edit', methods=['GET', 'POST'])
def edit(account_id=None):
    from studio import require_csrf
    account = get_account(account_id) if account_id else {'provider': 'gmail', 'smtp_host': 'smtp.gmail.com', 'smtp_port': 587, 'tls_mode': 'starttls'}
    if account is None or account.get('deleted'):
        abort(404)
    error = None
    if request.method == 'POST':
        require_csrf()
        try:
            save_account(request.form, request.form.get('smtp_password', ''), account_id)
            return redirect(url_for('senders.manage', saved=1))
        except TemplateValidationError as exc:
            error = str(exc)
        # Explicit allowlist: never echo request.form, password, ciphertext, or key.
        account = account | {name: request.form.get(name, '') for name in ('display_name','email','provider','smtp_host','smtp_port','tls_mode','smtp_username')}
    return render_template('sender_account_form.html', account=account, error=error)


@senders.route('/senders/<int:account_id>/<action>', methods=['POST'])
def action(account_id, action):
    from studio import require_csrf
    require_csrf()
    try:
        if action == 'test':
            verify_account(account_id)
        else:
            account_action(account_id, action)
        return redirect(url_for('senders.manage', verified=1 if action == 'test' else 0))
    except TemplateValidationError as exc:
        return render_template('sender_accounts.html', accounts=list_accounts(), error=str(exc)), 400
