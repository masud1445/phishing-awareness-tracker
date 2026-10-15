import os
import secrets
import json


from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session
from werkzeug.exceptions import HTTPException, SecurityError
from database import get_db_connection
from email_service import EmailDeliveryError, send_campaign_email, build_tracking_url, effective_template
from template_content import TemplateValidationError, render_email, preview_digest
from studio import configure_studio, require_csrf

load_dotenv()

app = Flask(__name__)
configure_studio(app)


@app.errorhandler(HTTPException)
def product_http_error(error):
    response = error.get_response()
    if isinstance(error, SecurityError):
        return response  # Untrusted hosts have no URL adapter for branded templates.
    response.set_data(render_template('error.html',code=error.code,message=error.description,public=request.path.startswith('/t/')))
    response.content_type = 'text/html; charset=utf-8'
    return response


@app.after_request
def product_validation_response(response):
    # Existing endpoint validations keep their status and message in a useful page.
    if response.status_code >= 400 and response.mimetype == 'text/html':
        text = response.get_data(as_text=True)
        if '<' not in text:
            response.set_data(render_template('error.html',code=response.status_code,message=text,public=request.path.startswith('/t/')))
    return response


def campaign_overview():
    """One aggregate row per campaign; click metrics use approved audiences only."""
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT c.id,c.campaign_name,c.campaign_template,c.start_date,c.status,
                COUNT(DISTINCT r.id) AS recipient_count,
                COUNT(DISTINCT CASE WHEN r.approved=1 THEN r.id END) AS approved_count,
                COUNT(DISTINCT CASE WHEN r.approved=1 AND r.sent_at IS NOT NULL THEN r.id END) AS sent_count,
                COUNT(DISTINCT CASE WHEN r.approved=1 AND te.event_type='link_clicked' THEN r.id END) AS click_count
                FROM campaigns c LEFT JOIN recipients r ON r.campaign_id=c.id
                LEFT JOIN tracking_events te ON te.recipient_id=r.id AND te.campaign_id=c.id
                GROUP BY c.id,c.campaign_name,c.campaign_template,c.start_date,c.status,c.created_at
                ORDER BY c.created_at DESC,c.id DESC""")
            campaigns = list(cursor.fetchall())
    finally:
        connection.close()
    for campaign in campaigns:
        approved = campaign['approved_count']
        campaign['click_rate'] = round(100 * campaign['click_count'] / approved, 1) if approved else 0.0
        campaign['awareness_score'] = round(100 - campaign['click_rate'], 1) if approved else None
    return campaigns


def overview_context():
    campaigns = campaign_overview()
    recipients = sum(c['approved_count'] for c in campaigns)
    clicks = sum(c['click_count'] for c in campaigns)
    return dict(campaigns=campaigns,total_campaigns=len(campaigns),total_recipients=recipients,
                link_clicks=clicks,awareness_score=round(100 * (recipients-clicks)/recipients,1) if recipients else None,
                sent_count=sum(c['sent_count'] for c in campaigns),
                active_count=sum(c['status']=='active' for c in campaigns),
                draft_count=sum(c['status']=='draft' for c in campaigns))


@app.route("/")
def home():
    return render_template('index.html', **overview_context())


@app.route('/campaigns')
def campaign_directory():
    return render_template('campaigns.html', **overview_context())


@app.route('/recipients')
def recipient_directory():
    connection = get_db_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""SELECT r.id,r.full_name,r.email,r.department,r.approved,r.status,r.campaign_id,
                c.campaign_name FROM recipients r JOIN campaigns c ON c.id=r.campaign_id
                ORDER BY r.created_at DESC,r.id DESC""")
            recipients = cursor.fetchall()
    finally:
        connection.close()
    return render_template('recipient_directory.html',recipients=recipients)


@app.route('/analytics')
def analytics_overview():
    return render_template('analytics.html', **overview_context())


@app.route('/settings')
def platform_settings():
    from sender_accounts import list_accounts, selectable_accounts
    from cryptography.fernet import Fernet
    try:
        Fernet(os.getenv('SENDER_ENCRYPTION_KEY','').encode())
        encryption_ready = True
    except (ValueError,TypeError):
        encryption_ready = False
    return render_template('settings.html',accounts=list_accounts(),verified_count=len(selectable_accounts()),
                           encryption_ready=encryption_ready,https_cookies=app.config['SESSION_COOKIE_SECURE'])


@app.route("/campaigns/new", methods=["GET", "POST"])
def create_campaign():
    if request.method == "POST":
        campaign_name = request.form.get("campaign_name", "").strip()
        purpose = request.form.get("purpose", "").strip()
        campaign_template = request.form.get("campaign_template", "").strip()
        start_date = request.form.get("start_date", "").strip()
        authorized = request.form.get("authorized") == "on"

        if not campaign_name or not campaign_template or not start_date:
            return "Missing required campaign information.", 400

        if not authorized:
            return "Authorization confirmation is required.", 400

        connection = get_db_connection()

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO campaigns
                    (
                        campaign_name,
                        purpose,
                        campaign_template,
                        start_date,
                        authorized,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        campaign_name,
                        purpose,
                        campaign_template,
                        start_date,
                        True,
                        "draft",
                    ),
                )

                campaign_id = cursor.lastrowid

        finally:
            connection.close()

        return redirect(
            url_for("campaign_recipients", campaign_id=campaign_id)
        )

    return render_template("create_campaign.html")


@app.route("/campaigns/<int:campaign_id>/recipients")
def campaign_recipients(campaign_id):
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    campaign_name,
                    purpose,
                    campaign_template,
                    start_date,
                    status
                FROM campaigns
                WHERE id = %s
                """,
                (campaign_id,),
            )

            campaign = cursor.fetchone()

            if campaign is None:
                return "Campaign not found.", 404

            cursor.execute(
                """
                SELECT
                    id,
                    full_name,
                    email,
                    department,
                    approved,
                    approved_at,
                    status,
                    created_at
                FROM recipients
                WHERE campaign_id = %s
                ORDER BY created_at DESC
                """,
                (campaign_id,),
            )

            recipients = cursor.fetchall()

    finally:
        connection.close()

    return render_template(
        "recipients.html",
        campaign=campaign,
        recipients=recipients,
    )


@app.route(
    "/campaigns/<int:campaign_id>/recipients/new",
    methods=["GET", "POST"],
)
def add_recipient(campaign_id):
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, campaign_name
                FROM campaigns
                WHERE id = %s
                """,
                (campaign_id,),
            )

            campaign = cursor.fetchone()

            if campaign is None:
                return "Campaign not found.", 404

            if request.method == "POST":
                full_name = request.form.get("full_name", "").strip()
                email = request.form.get("email", "").strip().lower()
                department = request.form.get("department", "").strip()

                if not full_name or not email:
                    return "Name and email are required.", 400

                tracking_token = secrets.token_urlsafe(24)

                cursor.execute(
                    """
                    INSERT INTO recipients
                    (
                        campaign_id,
                        full_name,
                        email,
                        department,
                        tracking_token,
                        status
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        campaign_id,
                        full_name,
                        email,
                        department or None,
                        tracking_token,
                        "pending",
                    ),
                )

                return redirect(
                    url_for(
                        "campaign_recipients",
                        campaign_id=campaign_id,
                    )
                )

    finally:
        connection.close()

    return render_template(
        "add_recipient.html",
        campaign=campaign,
    )


@app.route("/campaigns/<int:campaign_id>/launch", methods=["POST"])
def launch_campaign(campaign_id):
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) AS total
                FROM recipients
                WHERE campaign_id = %s
                """,
                (campaign_id,),
            )

            recipient_count = cursor.fetchone()["total"]

            if recipient_count == 0:
                return "Add at least one recipient before launching the campaign.", 400

            cursor.execute(
                """
                UPDATE campaigns
                SET status = 'active'
                WHERE id = %s
                """,
                (campaign_id,),
            )

    finally:
        connection.close()

    return redirect(
        url_for("campaign_recipients", campaign_id=campaign_id)
    )


@app.route(
    "/campaigns/<int:campaign_id>/recipients/<int:recipient_id>/approve",
    methods=["POST"],
)
def approve_recipient(campaign_id, recipient_id):
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE recipients
                SET
                    approved = TRUE,
                    approved_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND campaign_id = %s
                """,
                (recipient_id, campaign_id),
            )

    finally:
        connection.close()

    return redirect(
        url_for(
            "campaign_recipients",
            campaign_id=campaign_id,
        )
    )


@app.route(
    "/campaigns/<int:campaign_id>/recipients/<int:recipient_id>/send-email",
    methods=["POST"],
)
def send_recipient_email(campaign_id, recipient_id):
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    campaign_name,
                    status,
                    authorized,
                    (SELECT snapshot FROM campaign_email_settings WHERE campaign_id = campaigns.id) AS email_snapshot
                FROM campaigns
                WHERE id = %s
                """,
                (campaign_id,),
            )
            campaign = cursor.fetchone()

            if campaign is None:
                return "Campaign not found.", 404

            cursor.execute(
                """
                SELECT
                    id,
                    campaign_id,
                    full_name,
                    email,
                    approved,
                    tracking_token,
                    status,
                    sent_at
                FROM recipients
                WHERE id = %s
                  AND campaign_id = %s
                  AND approved = TRUE
                """,
                (recipient_id, campaign_id),
            )
            recipient = cursor.fetchone()

            if recipient is None:
                return "Recipient is not approved for email delivery.", 403

            if campaign.get("email_snapshot"):
                # Serialize Studio delivery with template replacement. Re-read under
                # the same campaign lock used by apply_template before validation.
                connection.begin()
                cursor.execute(
                    "SELECT id, campaign_name, status, authorized, "
                    "(SELECT snapshot FROM campaign_email_settings WHERE campaign_id=campaigns.id) AS email_snapshot "
                    "FROM campaigns WHERE id=%s FOR UPDATE", (campaign_id,))
                campaign = cursor.fetchone()
                campaign["email_template"] = json.loads(campaign["email_snapshot"])
                require_csrf()
                if request.form.get("authorized") != "on":
                    return "Authorization confirmation is required.", 400
                try:
                    if campaign["email_template"].get("sender_account_id"):
                        from sender_accounts import lock_account
                        lock_account(campaign["email_template"]["sender_account_id"], connection)
                        campaign["_delivery_connection"] = connection
                    digest = preview_digest(campaign["email_template"], campaign, recipient,
                                            build_tracking_url(recipient["tracking_token"]))
                except (TemplateValidationError, EmailDeliveryError) as exc:
                    return str(exc), 400
                if session.get("reviewed_email") != [campaign_id, recipient_id, digest]:
                    return "Preview the current recipient email before sending.", 400
            if campaign.get("authorized") == 0:
                return "Campaign authorization confirmation is required.", 400

            try:
                send_campaign_email(campaign, recipient)

            except EmailDeliveryError as exc:
                return (
                    f"Email delivery blocked: {exc}",
                    400,
                )

            cursor.execute(
                """
                UPDATE recipients
                SET
                    sent_at = COALESCE(sent_at, CURRENT_TIMESTAMP),
                    status = CASE
                        WHEN status = 'clicked' THEN status
                        ELSE 'sent'
                    END
                WHERE id = %s
                """,
                (recipient_id,),
            )
            if campaign.get("email_template"):
                connection.commit()

    finally:
        connection.close()

    return redirect(
        url_for(
            "campaign_email_preview",
            campaign_id=campaign_id,
            delivery="success",
        )
    )


@app.route("/campaigns/<int:campaign_id>/email-preview")
def campaign_email_preview(campaign_id):
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    campaign_name,
                    purpose,
                    campaign_template,
                    start_date,
                    status,
                    authorized,
                    (SELECT snapshot FROM campaign_email_settings WHERE campaign_id = campaigns.id) AS email_snapshot
                FROM campaigns
                WHERE id = %s
                """,
                (campaign_id,),
            )

            campaign = cursor.fetchone()

            if campaign is None:
                return "Campaign not found.", 404

            cursor.execute(
                """
                SELECT
                    id,
                    full_name,
                    email,
                    department,
                    tracking_token,
                    status
                FROM recipients
                WHERE campaign_id = %s
                  AND approved = TRUE
                ORDER BY id ASC
                """,
                (campaign_id,),
            )

            recipients = cursor.fetchall()

    finally:
        connection.close()

    if campaign.get("email_snapshot"):
        campaign["email_template"] = json.loads(campaign["email_snapshot"])
    recipient_id = request.args.get("recipient_id", type=int)
    selected = next((r for r in recipients if r["id"] == recipient_id), None) if recipient_id else (recipients[0] if recipients else None)
    if recipient_id and selected is None:
        return "Approved recipient not found.", 404
    template = effective_template(campaign)
    rendered = None
    sender_unavailable = None
    if selected:
        try:
            illustration = next((a for a in template.get("assets", []) if a["kind"] == "image"), None)
            tracking_url = build_tracking_url(selected["tracking_token"])
            rendered = render_email(template, campaign, selected, tracking_url,
                                    url_for("studio.asset_download", asset_id=illustration["id"]) if illustration else None)
            if campaign.get("email_template"):
                try:
                    session["reviewed_email"] = [campaign_id, selected["id"], preview_digest(template, campaign, selected, tracking_url)]
                except TemplateValidationError as exc:
                    session.pop("reviewed_email", None)
                    sender_unavailable = str(exc)
        except (TemplateValidationError, EmailDeliveryError) as exc:
            return str(exc), 400

    return render_template(
        "email_preview.html",
        selected=selected, rendered=rendered, template=template, sender_unavailable=sender_unavailable,
        campaign=campaign,
        recipients=recipients,
        base_url=os.getenv(
            "BASE_URL",
            "http://127.0.0.1:5055"
        ).rstrip("/"),
    )



@app.route("/campaigns/<int:campaign_id>/results")
def campaign_results(campaign_id):
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id,
                    campaign_name,
                    purpose,
                    campaign_template,
                    start_date,
                    status,
                    created_at
                FROM campaigns
                WHERE id = %s
                """,
                (campaign_id,),
            )

            campaign = cursor.fetchone()

            if campaign is None:
                return "Campaign not found.", 404

            cursor.execute(
                """
                SELECT
                    r.id,
                    r.full_name,
                    r.email,
                    r.department,
                    r.approved,
                    r.approved_at,
                    r.status,
                    r.sent_at,
                    (
                        SELECT MIN(te.event_time)
                        FROM tracking_events te
                        WHERE te.recipient_id = r.id
                          AND te.event_type = 'link_clicked'
                    ) AS clicked_at
                FROM recipients r
                WHERE r.campaign_id = %s
                ORDER BY r.created_at ASC
                """,
                (campaign_id,),
            )

            recipients = cursor.fetchall()

    finally:
        connection.close()

    approved_recipients = [
        recipient
        for recipient in recipients
        if recipient["approved"]
    ]

    total_recipients = len(approved_recipients)

    sent_count = sum(
        1
        for recipient in approved_recipients
        if recipient["sent_at"] is not None
    )

    clicked_count = sum(
        1
        for recipient in approved_recipients
        if recipient["clicked_at"] is not None
    )

    if total_recipients > 0:
        click_rate = round(
            (clicked_count / total_recipients) * 100,
            1,
        )

        awareness_score = round(
            ((total_recipients - clicked_count) / total_recipients) * 100,
            1,
        )
    else:
        click_rate = 0.0
        awareness_score = None

    return render_template(
        "campaign_results.html",
        campaign=campaign,
        recipients=recipients,
        total_recipients=total_recipients,
        sent_count=sent_count,
        clicked_count=clicked_count,
        click_rate=click_rate,
        awareness_score=awareness_score,
    )


@app.route("/t/<tracking_token>")
def track_recipient_click(tracking_token):
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    r.id AS recipient_id,
                    r.campaign_id,
                    r.full_name,
                    r.status,
                    c.campaign_name
                FROM recipients r
                JOIN campaigns c
                    ON c.id = r.campaign_id
                WHERE r.tracking_token = %s
                """,
                (tracking_token,),
            )

            recipient = cursor.fetchone()

            if recipient is None:
                return "Invalid or expired campaign link.", 404

            cursor.execute(
                """
                SELECT id
                FROM tracking_events
                WHERE recipient_id = %s
                  AND event_type = 'link_clicked'
                LIMIT 1
                """,
                (recipient["recipient_id"],),
            )

            existing_click = cursor.fetchone()

            if existing_click is None:
                cursor.execute(
                    """
                    INSERT INTO tracking_events
                    (
                        campaign_id,
                        recipient_id,
                        event_type
                    )
                    VALUES (%s, %s, %s)
                    """,
                    (
                        recipient["campaign_id"],
                        recipient["recipient_id"],
                        "link_clicked",
                    ),
                )

            cursor.execute(
                """
                UPDATE recipients
                SET status = 'clicked'
                WHERE id = %s
                """,
                (recipient["recipient_id"],),
            )

    finally:
        connection.close()

    return render_template(
        "awareness_landing.html",
        recipient=recipient,
    )


if __name__ == "__main__":
    app.run(host=os.getenv("APP_HOST", "127.0.0.1"), debug=False, port=5055)
