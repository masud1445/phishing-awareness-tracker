import os
import secrets

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for
from database import get_db_connection
from email_service import EmailDeliveryError, send_campaign_email

load_dotenv()

app = Flask(__name__)


@app.route("/")
def home():
    connection = get_db_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM campaigns")
            total_campaigns = cursor.fetchone()["total"]

            cursor.execute("SELECT COUNT(*) AS total FROM recipients WHERE approved = 1")
            total_recipients = cursor.fetchone()["total"]

            cursor.execute(
                """
                SELECT COUNT(DISTINCT recipient_id) AS total
                FROM tracking_events
                WHERE event_type = 'link_clicked'
                """
            )
            link_clicks = cursor.fetchone()["total"]

            if total_recipients > 0:
                awareness_score = round(
                    ((total_recipients - link_clicks) / total_recipients) * 100,
                    1,
                )
            else:
                awareness_score = None

            cursor.execute(
                """
                SELECT
                    c.id,
                    c.campaign_name,
                    c.campaign_template,
                    c.start_date,
                    c.status,
                    COUNT(DISTINCT r.id) AS recipient_count,
                    COUNT(
                        DISTINCT CASE
                            WHEN te.event_type = 'link_clicked'
                            THEN te.recipient_id
                        END
                    ) AS click_count
                FROM campaigns c
                LEFT JOIN recipients r
                    ON r.campaign_id = c.id
                LEFT JOIN tracking_events te
                    ON te.campaign_id = c.id
                GROUP BY
                    c.id,
                    c.campaign_name,
                    c.campaign_template,
                    c.start_date,
                    c.status
                ORDER BY c.created_at DESC
                LIMIT 5
                """
            )

            campaigns = cursor.fetchall()

    finally:
        connection.close()

    return render_template(
        "index.html",
        total_campaigns=total_campaigns,
        total_recipients=total_recipients,
        link_clicks=link_clicks,
        awareness_score=awareness_score,
        campaigns=campaigns,
    )


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

    return render_template(
        "email_preview.html",
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
    app.run(host="0.0.0.0", debug=False, port=5055)
