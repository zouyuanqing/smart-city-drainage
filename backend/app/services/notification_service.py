import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any

import httpx
from app.core.config import settings

logger = logging.getLogger(__name__)


class NotificationService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

    async def send_email(self, to: str, subject: str, body: str, html: str | None = None) -> bool:
        if not settings.SMTP_ENABLED:
            logger.debug("SMTP disabled, skipping email to %s", to)
            return False
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = settings.SMTP_FROM
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))
            if html:
                msg.attach(MIMEText(html, "html", "utf-8"))

            if settings.SMTP_USE_TLS:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
                server.starttls()
            else:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)

            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)

            server.sendmail(settings.SMTP_FROM, [to], msg.as_string())
            server.quit()
            logger.info("Email sent to %s: %s", to, subject)
            return True
        except Exception as e:
            logger.error("Failed to send email to %s: %s", to, e)
            return False

    async def send_webhook(self, url: str, payload: dict[str, Any]) -> bool:
        if not settings.WEBHOOK_ENABLED:
            logger.debug("Webhook disabled, skipping %s", url)
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, json=payload)
                logger.info("Webhook sent to %s: status %d", url, response.status_code)
                return response.status_code < 400
        except Exception as e:
            logger.error("Failed to send webhook to %s: %s", url, e)
            return False

    async def notify_alert(self, alert: dict[str, Any]) -> None:
        level = alert.get("level", "info")
        title = alert.get("title", "Unknown Alert")
        description = alert.get("description", "")

        if level not in ("critical", "warning"):
            return

        subject = f"[SCN Alert][{level.upper()}] {title}"
        body = f"Alert Level: {level}\nTitle: {title}\nDescription: {description}\nTime: {alert.get('timestamp', 'N/A')}"

        html = f"""
        <html><body>
        <h2 style="color: {'red' if level == 'critical' else 'orange'}">[{level.upper()}] {title}</h2>
        <p><strong>Description:</strong> {description}</p>
        <p><strong>Time:</strong> {alert.get('timestamp', 'N/A')}</p>
        <p><strong>Device:</strong> {alert.get('device_id', 'N/A')}</p>
        </body></html>
        """

        if settings.SMTP_ENABLED and settings.ALERT_EMAIL_RECIPIENTS:
            for recipient in settings.ALERT_EMAIL_RECIPIENTS:
                await self.send_email(recipient, subject, body, html)

        if settings.WEBHOOK_ENABLED and settings.ALERT_WEBHOOK_URLS:
            for url in settings.ALERT_WEBHOOK_URLS:
                await self.send_webhook(url, {
                    "alert": alert,
                    "level": level,
                    "title": title,
                    "timestamp": alert.get("timestamp"),
                })


notification_service = NotificationService()
