import os
import smtplib
from email.message import EmailMessage
from html import escape

from dotenv import load_dotenv

load_dotenv()


class EmailDeliveryError(Exception):
    pass


def build_tracking_url(tracking_token):
    base_url = os.getenv(
        "BASE_URL",
        "http://127.0.0.1:5055"
    ).rstrip("/")

    return f"{base_url}/t/{tracking_token}"


def send_campaign_email(campaign, recipient):
    # Security enforcement
    if campaign.get("status") != "active":
        raise EmailDeliveryError(
            "Campaign must be active before email delivery."
        )

    if not recipient.get("approved"):
        raise EmailDeliveryError(
            "Recipient is not approved for this campaign."
        )

    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")
    sender_email = os.getenv("SMTP_FROM_EMAIL")
    sender_name = os.getenv(
        "SMTP_FROM_NAME",
        "Security Awareness Team"
    )

    required = {
        "SMTP_HOST": smtp_host,
        "SMTP_USER": smtp_user,
        "SMTP_PASSWORD": smtp_password,
        "SMTP_FROM_EMAIL": sender_email,
    }

    missing = [
        key for key, value in required.items()
        if not value
    ]

    if missing:
        raise EmailDeliveryError(
            "SMTP configuration missing: "
            + ", ".join(missing)
        )

    tracking_url = build_tracking_url(
        recipient["tracking_token"]
    )

    recipient_name = escape(recipient["full_name"])
    campaign_name = escape(campaign["campaign_name"])

    message = EmailMessage()

    message["Subject"] = (
        "Security Awareness: Review Required Information"
    )

    message["From"] = f"{sender_name} <{sender_email}>"
    message["To"] = recipient["email"]

    message.set_content(
        f"""Hello {recipient["full_name"]},

You have been included in the authorized security awareness campaign:
{campaign["campaign_name"]}

Please review the information using the link below:

{tracking_url}

This campaign does not request passwords, OTPs, or authentication credentials.

Security Awareness Team
"""
    )

    message.add_alternative(
        f"""
        <!doctype html>
        <html>
        <body style="
            font-family: Arial, sans-serif;
            background:#f4f7fb;
            padding:30px;
            color:#172033;
        ">

            <div style="
                max-width:600px;
                margin:auto;
                background:white;
                border-radius:16px;
                padding:32px;
                border:1px solid #e3e8f0;
            ">

                <div style="
                    color:#2563eb;
                    font-weight:700;
                    margin-bottom:24px;
                ">
                    Security Awareness
                </div>

                <h2>Hello {recipient_name},</h2>

                <p>
                    You have been included in the authorized
                    security awareness campaign
                    <strong>{campaign_name}</strong>.
                </p>

                <p>
                    Please review the information using the
                    secure campaign link below.
                </p>

                <p style="margin:28px 0;">
                    <a href="{tracking_url}"
                       style="
                           background:#2563eb;
                           color:white;
                           text-decoration:none;
                           padding:12px 20px;
                           border-radius:9px;
                           font-weight:700;
                       ">
                        Review Information
                    </a>
                </p>

                <p style="
                    color:#64748b;
                    font-size:12px;
                    margin-top:28px;
                ">
                    This awareness campaign does not request
                    passwords, OTPs, or authentication credentials.
                </p>

            </div>

        </body>
        </html>
        """,
        subtype="html",
    )

    with smtplib.SMTP(
        smtp_host,
        smtp_port,
        timeout=20
    ) as server:

        server.starttls()
        server.login(
            smtp_user,
            smtp_password
        )

        server.send_message(message)

    return True
