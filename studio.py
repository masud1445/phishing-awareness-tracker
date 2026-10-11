"""Template Studio views. No SMTP secrets or participant credential fields."""
import io
import secrets

from flask import Blueprint, abort, redirect, render_template, request, send_file, session, url_for

from database import get_db_connection
from email_service import EmailDeliveryError, build_tracking_url
from template_content import DEFAULT_TEMPLATE, TemplateValidationError, render_email, sender_addresses, validate_template
from template_store import apply_template, get_asset, get_template, list_templates, save_template
from uploads import validate_upload

studio = Blueprint('studio', __name__)


def csrf_token():
    if 'studio_csrf' not in session:
        session['studio_csrf'] = secrets.token_urlsafe(32)
    return session['studio_csrf']


def require_csrf():
    token = session.get('studio_csrf', '')
    if not token or not secrets.compare_digest(token, request.form.get('csrf_token', '')):
        abort(400, 'Invalid form token. Reload the page and try again.')


@studio.app_context_processor
def tokens():
    return {'studio_csrf': csrf_token}


@studio.route('/templates')
def library():
    return render_template('template_library.html', templates=list_templates(), senders=sender_addresses())


@studio.route('/templates/new', methods=['GET', 'POST'])
@studio.route('/templates/<int:template_id>/edit', methods=['GET', 'POST'])
def editor(template_id=None):
    template = get_template(template_id) if template_id else DEFAULT_TEMPLATE.copy()
    if template is None:
        abort(404)
    error = None
    preview = None
    if request.method == 'POST':
        require_csrf()
        try:
            values = validate_template(request.form)
            if request.form.get('action') == 'preview':
                preview = render_email(values, {'campaign_name': 'Example awareness campaign'},
                                       {'full_name': 'Alex Morgan'}, build_tracking_url('PREVIEW-ONLY'))
            else:
                assets = []
                image = request.files.get('image')
                if image and image.filename:
                    assets.append(validate_upload(image, 'image'))
                files = [file for file in request.files.getlist('attachments') if file.filename]
                if len(files) > 5:
                    raise TemplateValidationError('Use at most five attachments.')
                assets.extend(validate_upload(file) for file in files)
                saved_id = save_template(values, assets, template_id, request.form.getlist('remove_assets'))
                return redirect(url_for('studio.editor', template_id=saved_id, saved=1))
        except (TemplateValidationError, EmailDeliveryError) as exc:
            error = str(exc)
        template = template | {key: request.form.get(key, '') for key in ('name', 'subject', 'body', 'sender_name', 'sender_email', 'reference_link')}
    return render_template('template_editor.html', template=template, error=error, preview=preview, senders=sender_addresses())


@studio.route('/campaigns/<int:campaign_id>/studio', methods=['GET', 'POST'])
def campaign_studio(campaign_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id, campaign_name, status FROM campaigns WHERE id=%s', (campaign_id,))
            campaign = cursor.fetchone()
    finally:
        connection.close()
    if campaign is None:
        abort(404)
    error = None
    if request.method == 'POST':
        require_csrf()
        try:
            template_id = int(request.form.get('template_id', '0'))
            template = get_template(template_id)
            if template is None:
                raise TemplateValidationError('Choose an existing template.')
            validate_template(template)
            if request.form.get('authorized') != 'on':
                raise TemplateValidationError('Authorization confirmation is required.')
            apply_template(campaign_id, template)
            return redirect(url_for('campaign_email_preview', campaign_id=campaign_id))
        except (TemplateValidationError, ValueError) as exc:
            error = str(exc)
    return render_template('campaign_studio.html', campaign=campaign, templates=list_templates(), error=error)


@studio.route('/template-assets/<int:asset_id>')
def asset_download(asset_id):
    asset = get_asset(asset_id)
    if asset is None:
        abort(404)
    response = send_file(io.BytesIO(asset['data']), mimetype=asset['mime_type'],
                         as_attachment=asset['kind'] != 'image', download_name=asset['filename'], max_age=0)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'; sandbox"
    response.headers['Cache-Control'] = 'no-store'
    return response


def configure_studio(app):
    import os
    from pathlib import Path
    # Persistent local signing key; shared production instances should set SECRET_KEY.
    key = os.getenv('SECRET_KEY')
    if not key:
        path = Path(app.instance_path) / 'session.key'
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as file:
                file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        key = path.read_text().strip()
    app.config.update(SECRET_KEY=key, MAX_CONTENT_LENGTH=32 * 1024 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
                      SESSION_COOKIE_SECURE=os.getenv('SESSION_COOKIE_SECURE', '0') == '1')
    app.register_blueprint(studio)

    @app.before_request
    def enforce_upload_request_limit():
        if request.content_length and request.content_length > app.config['MAX_CONTENT_LENGTH']:
            abort(413, 'Request too large. Each file is limited to 5 MB.')

    @app.cli.command('init-db')
    def init_db_command():
        from database import initialize_database
        initialize_database()
        print('Database tables initialized without resetting existing data.')
