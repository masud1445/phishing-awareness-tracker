"""Parameterized persistence and immutable campaign snapshots."""
import hashlib
import json

from database import get_db_connection
from template_content import TemplateValidationError

FIELDS = ('name', 'subject', 'body', 'sender_name', 'sender_email', 'reference_link', 'sender_account_id', 'presentation')


def list_templates():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT id, name, subject, body, sender_name, sender_email, updated_at, (SELECT COUNT(*) FROM template_assets a WHERE a.template_id=email_templates.id AND a.archived=0) AS asset_count FROM email_templates ORDER BY id DESC')
            return list(cursor.fetchall())
    finally:
        connection.close()


def get_template(template_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM email_templates WHERE id = %s', (template_id,))
            template = cursor.fetchone()
            if template:
                template['presentation'] = json.loads(template.get('presentation') or '{}')
                cursor.execute("SELECT id, filename, mime_type, kind, data FROM template_assets WHERE template_id = %s AND archived = 0 ORDER BY id", (template_id,))
                assets = cursor.fetchall()
                template['assets'] = [asset_metadata(asset) for asset in assets]
            return template
    finally:
        connection.close()


def asset_metadata(asset):
    return {key: asset[key] for key in ('id', 'filename', 'mime_type', 'kind')} | {
        'sha256': hashlib.sha256(asset['data']).hexdigest(), 'size': len(asset['data'])}


def get_asset(asset_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM template_assets WHERE id = %s', (asset_id,))
            return cursor.fetchone()
    finally:
        connection.close()


def save_template(values, assets, template_id=None, remove_ids=()):
    values = values | {'presentation': json.dumps(values.get('presentation') or {})}
    connection = get_db_connection()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            if template_id:
                cursor.execute('SELECT id FROM email_templates WHERE id = %s FOR UPDATE', (template_id,))
                if not cursor.fetchone():
                    raise TemplateValidationError('Template not found.')
                cursor.execute("SELECT id, kind FROM template_assets WHERE template_id = %s AND archived = 0", (template_id,))
                retained = [asset for asset in cursor.fetchall() if str(asset['id']) not in remove_ids]
            else:
                retained = []
            combined = retained + assets
            if sum(asset['kind'] == 'image' for asset in combined) > 1 or sum(asset['kind'] == 'attachment' for asset in combined) > 5:
                raise TemplateValidationError('Use at most one illustration and five attachments. Remove existing files before replacing them.')
            if template_id:
                # Old asset rows remain available to immutable campaign snapshots.
                # Preserve their bytes by ID for campaign snapshots.
                cursor.execute('UPDATE email_templates SET name=%s, subject=%s, body=%s, sender_name=%s, sender_email=%s, reference_link=%s, sender_account_id=%s, presentation=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s', tuple(values.get(key) for key in FIELDS) + (template_id,))
                for asset_id in remove_ids:
                    cursor.execute('UPDATE template_assets SET archived=1 WHERE id=%s AND template_id=%s', (asset_id, template_id))
            else:
                cursor.execute('INSERT INTO email_templates (name, subject, body, sender_name, sender_email, reference_link, sender_account_id, presentation) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)', tuple(values.get(key) for key in FIELDS))
                template_id = cursor.lastrowid
            for asset in assets:
                cursor.execute('INSERT INTO template_assets (template_id, filename, mime_type, kind, data) VALUES (%s,%s,%s,%s,%s)', (template_id, asset['filename'], asset['mime_type'], asset['kind'], asset['data']))
        connection.commit()
        return template_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def apply_template(campaign_id, template):
    snapshot = {key: template.get(key) for key in FIELDS}
    snapshot['assets'] = template['assets']
    connection = get_db_connection()
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute('SELECT id, authorized, status FROM campaigns WHERE id=%s FOR UPDATE', (campaign_id,))
            campaign = cursor.fetchone()
            if not campaign:
                raise TemplateValidationError('Campaign not found.')
            if not campaign['authorized']:
                raise TemplateValidationError('Campaign authorization confirmation is required.')
            cursor.execute('SELECT COUNT(*) AS total FROM recipients WHERE campaign_id=%s AND sent_at IS NOT NULL', (campaign_id,))
            if cursor.fetchone()['total']:
                raise TemplateValidationError('Template is locked after the first successful delivery. Create a new campaign to change it.')
            cursor.execute('DELETE FROM campaign_email_settings WHERE campaign_id=%s', (campaign_id,))
            cursor.execute('INSERT INTO campaign_email_settings (campaign_id, template_id, snapshot) VALUES (%s,%s,%s)', (campaign_id, template['id'], json.dumps(snapshot)))
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def campaign_template(campaign_id):
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT snapshot FROM campaign_email_settings WHERE campaign_id=%s', (campaign_id,))
            row = cursor.fetchone()
            return json.loads(row['snapshot']) if row else None
    finally:
        connection.close()
