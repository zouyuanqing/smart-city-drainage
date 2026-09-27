"""Legacy 与 forecast 告警通知服务。"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import smtplib
import socket
from collections.abc import Mapping
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

FORECAST_NOTIFICATION_EVENTS = frozenset(
    {"alert.created", "alert.escalated", "alert.observed", "alert.resolved"}
)
FORECAST_EXTERNAL_LEVELS = frozenset({"warning", "critical"})
MAX_WEBHOOK_RESPONSE_BYTES = 1_048_576


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

    async def send_email(
        self, to: str, subject: str, body: str, html_body: str | None = None
    ) -> bool:
        """在线程池中发送 SMTP 邮件，失败只返回 ``False``。"""
        if not settings.SMTP_ENABLED:
            logger.debug("SMTP disabled, skipping email to %s", to)
            return False
        if not to or "\r" in to or "\n" in to:
            logger.warning("Refusing email with invalid recipient")
            return False

        safe_subject = self._header_safe(subject, fallback="Forecast notification")
        try:
            return await asyncio.to_thread(
                self._send_email_sync,
                to,
                safe_subject,
                body,
                html_body,
            )
        except Exception as exc:
            logger.error("Failed to send email to %s: %s", self._redact_email(to), exc)
            return False

    def _send_email_sync(
        self,
        to: str,
        subject: str,
        body: str,
        html_body: str | None,
    ) -> bool:
        server: smtplib.SMTP | None = None
        try:
            message = MIMEMultipart("alternative")
            message["From"] = settings.SMTP_FROM
            message["To"] = to
            message["Subject"] = subject
            message.attach(MIMEText(body, "plain", "utf-8"))
            if html_body:
                message.attach(MIMEText(html_body, "html", "utf-8"))

            server = smtplib.SMTP(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=10.0,
            )
            if settings.SMTP_USE_TLS:
                server.starttls()
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)

            server.sendmail(settings.SMTP_FROM, [to], message.as_string())
            logger.info("Email sent to %s: %s", self._redact_email(to), subject)
            return True
        except Exception as exc:
            logger.error("Failed to send email to %s: %s", self._redact_email(to), exc)
            return False
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    try:
                        server.close()
                    except Exception:
                        pass

    async def send_webhook(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
        signature_secret: str | None = None,
    ) -> bool:
        """Send a bounded, non-redirecting, signed forecast webhook."""

        if not settings.WEBHOOK_ENABLED:
            logger.debug("Webhook disabled, skipping %s", self._redact_url(url))
            return False
        if not self._is_https_webhook(url):
            logger.warning("Refusing webhook URL outside the public HTTPS allowlist")
            return False
        if settings.DEPLOYMENT_ENVIRONMENT == "production" and not signature_secret:
            logger.error("Production forecast webhook requires an HMAC secret")
            return False

        headers: dict[str, str] = {}
        if event_id:
            headers["X-Forecast-Event-Id"] = event_id
            headers["Idempotency-Key"] = event_id
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        ).encode("utf-8")
        if len(body) > MAX_WEBHOOK_RESPONSE_BYTES:
            return False
        if signature_secret:
            signature = hmac.new(
                signature_secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            headers["Content-Type"] = "application/json"
            headers["X-Forecast-Signature"] = f"sha256={signature}"
        else:
            headers["Content-Type"] = "application/json"

        try:
            async with httpx.AsyncClient(
                timeout=10.0, follow_redirects=False
            ) as client:
                if hasattr(client, "stream"):
                    async with client.stream(
                        "POST", url, content=body, headers=headers
                    ) as response:
                        if not 200 <= response.status_code < 300:
                            return False
                        declared = response.headers.get("content-length")
                        if declared and int(declared) > MAX_WEBHOOK_RESPONSE_BYTES:
                            return False
                        received = 0
                        async for chunk in response.aiter_bytes():
                            received += len(chunk)
                            if received > MAX_WEBHOOK_RESPONSE_BYTES:
                                return False
                        return True
                response = await client.post(url, content=body, headers=headers)
                if not 200 <= response.status_code < 300:
                    return False
                response_body = getattr(response, "content", b"") or b""
                return len(response_body) <= MAX_WEBHOOK_RESPONSE_BYTES
        except Exception as exc:
            logger.error("Failed to send webhook to %s: %s", self._redact_url(url), exc)
            return False

    def render_forecast_notification(self, event: Mapping[str, Any]) -> dict[str, Any]:
        """构建 forecast 邮件/HTML/webhook 模板。"""
        event_id = self._text(event.get("event_id"))
        event_type = self._text(event.get("event_type"), "alert.updated")
        alert_value = event.get("alert")
        alert: Mapping[str, Any] = (
            alert_value if isinstance(alert_value, Mapping) else event
        )

        level = self._text(alert.get("level"), "info").lower()
        phase = self._text(alert.get("phase"), "predicted").lower()
        alert_type = self._text(
            alert.get("type") or alert.get("alert_type"), "forecast"
        )
        target = self._format_target(alert)
        title = self._text(
            alert.get("title"),
            f"{alert_type} forecast alert for {target}",
        )
        description = self._text(
            alert.get("description"),
            f"phase={phase}; target={target}",
        )
        expected_at = self._text(
            alert.get("expected_at")
            or alert.get("issued_at")
            or event.get("occurred_at"),
            "N/A",
        )
        lead_time = self._text(alert.get("lead_time_minutes"), "N/A")
        measurement = self._format_measurement(alert.get("measurements"))
        confidence = self._text(alert.get("confidence"), "N/A")
        risk_score = self._text(alert.get("risk_score"), "N/A")
        simulation_id = self._text(
            alert.get("simulation_id") or event.get("simulation_id"), "N/A"
        )
        device = self._text(
            alert.get("device_id") or alert.get("target_id") or target, "N/A"
        )

        subject = self._header_safe(
            f"[SCN Forecast][{event_type}] {title}",
            fallback="SCN Forecast notification",
        )
        body = "\n".join(
            [
                f"Event Type: {event_type}",
                f"Alert Level: {level}",
                f"Title: {title}",
                f"Description: {description}",
                f"Phase: {phase}",
                f"Target: {target}",
                f"Expected At: {expected_at}",
                f"Lead Time (minutes): {lead_time}",
                f"Measurement: {measurement}",
                f"Confidence: {confidence}",
                f"Risk Score: {risk_score}",
                f"Simulation ID: {simulation_id}",
                f"Time: {expected_at}",
                f"Device: {device}",
                f"Event ID: {event_id}",
            ]
        )

        safe_level = html.escape(level, quote=True)
        safe_event_type = html.escape(event_type, quote=True)
        safe_title = html.escape(title, quote=True)
        safe_description = html.escape(description, quote=True)
        safe_phase = html.escape(phase, quote=True)
        safe_target = html.escape(target, quote=True)
        safe_expected_at = html.escape(expected_at, quote=True)
        safe_lead_time = html.escape(lead_time, quote=True)
        safe_measurement = html.escape(measurement, quote=True)
        safe_confidence = html.escape(confidence, quote=True)
        safe_risk_score = html.escape(risk_score, quote=True)
        safe_simulation_id = html.escape(simulation_id, quote=True)
        safe_device = html.escape(device, quote=True)
        safe_event_id = html.escape(event_id, quote=True)
        color = "#b91c1c" if level == "critical" else "#c2410c"
        html_body = f"""<!doctype html>
<html><body>
  <h2 style="color: {color}">[{safe_level}] {safe_title}</h2>
  <p><strong>Description:</strong> {safe_description}</p>
  <p><strong>Event:</strong> {safe_event_type}</p>
  <p><strong>Phase:</strong> {safe_phase}</p>
  <p><strong>Target:</strong> {safe_target}</p>
  <p><strong>Expected At:</strong> {safe_expected_at}</p>
  <p><strong>Lead Time (minutes):</strong> {safe_lead_time}</p>
  <p><strong>Measurement:</strong> {safe_measurement}</p>
  <p><strong>Confidence:</strong> {safe_confidence}</p>
  <p><strong>Risk Score:</strong> {safe_risk_score}</p>
  <p><strong>Simulation ID:</strong> {safe_simulation_id}</p>
  <p><strong>Time:</strong> {safe_expected_at}</p>
  <p><strong>Device:</strong> {safe_device}</p>
  <p><strong>Event ID:</strong> {safe_event_id}</p>
</body></html>"""

        webhook_payload = {
            "schema_version": int(event.get("schema_version", 1)),
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": self._text(event.get("occurred_at"), "N/A"),
            "network_id": self._text(
                event.get("network_id") or alert.get("network_id"), "N/A"
            ),
            "nowcast_run_id": self._text(
                event.get("nowcast_run_id") or alert.get("nowcast_run_id"), "N/A"
            ),
            "simulation_id": simulation_id,
            "alert": dict(alert),
        }
        return {
            "event_id": event_id,
            "event_type": event_type,
            "subject": subject,
            "body": body,
            "html": html_body,
            "webhook_payload": webhook_payload,
        }

    async def notify_forecast_event(
        self,
        event: Mapping[str, Any],
        *,
        webhook_secret: str | None = None,
    ) -> dict[str, int | str]:
        """
        投递 forecast 外部通知；每个目标独立失败，不影响其他目标。

        返回 attempted/succeeded/failed 计数，便于 outbox delivery worker 写入
        独立记录。Alert/outbox 本身不会因本方法失败而回滚。
        """
        event_id = self._text(event.get("event_id"))
        event_type = self._text(event.get("event_type"), "alert.updated")
        alert_value = event.get("alert")
        alert: Mapping[str, Any] = (
            alert_value if isinstance(alert_value, Mapping) else event
        )
        level = self._text(alert.get("level"), "info").lower()

        if not event_id:
            return {
                "event_id": "",
                "event_type": event_type,
                "email_attempted": 0,
                "email_succeeded": 0,
                "email_failed": 0,
                "webhook_attempted": 0,
                "webhook_succeeded": 0,
                "webhook_failed": 0,
            }

        should_notify = event_type in FORECAST_NOTIFICATION_EVENTS
        if event_type == "alert.resolved":
            should_notify = level == "critical"
        else:
            should_notify = should_notify and level in FORECAST_EXTERNAL_LEVELS
        if not should_notify:
            return {
                "event_id": event_id,
                "event_type": event_type,
                "email_attempted": 0,
                "email_succeeded": 0,
                "email_failed": 0,
                "webhook_attempted": 0,
                "webhook_succeeded": 0,
                "webhook_failed": 0,
            }

        template = self.render_forecast_notification(event)
        recipients = list(settings.ALERT_EMAIL_RECIPIENTS)
        urls = list(settings.ALERT_WEBHOOK_URLS)
        if settings.DEPLOYMENT_ENVIRONMENT == "production" and not webhook_secret:
            urls = []
        email_results = await asyncio.gather(
            *(
                self._send_email_isolated(
                    recipient,
                    str(template["subject"]),
                    str(template["body"]),
                    str(template["html"]),
                )
                for recipient in recipients
            )
        )

        webhook_results: list[bool] = []
        invalid_webhook_count = 0
        for url in urls:
            if not self._is_https_webhook(url):
                invalid_webhook_count += 1
                continue
            webhook_results.append(
                await self._send_webhook_isolated(
                    url,
                    dict(template["webhook_payload"]),
                    event_id=event_id,
                    signature_secret=webhook_secret,
                )
            )

        email_succeeded = sum(1 for result in email_results if result)
        webhook_succeeded = sum(1 for result in webhook_results if result)
        email_attempted = len(email_results)
        webhook_attempted = len(webhook_results) + invalid_webhook_count
        webhook_failed = (
            len(webhook_results) - webhook_succeeded + invalid_webhook_count
        )
        return {
            "event_id": event_id,
            "event_type": event_type,
            "email_attempted": email_attempted,
            "email_succeeded": email_succeeded,
            "email_failed": email_attempted - email_succeeded,
            "webhook_attempted": webhook_attempted,
            "webhook_succeeded": webhook_succeeded,
            "webhook_failed": webhook_failed,
        }

    async def _send_email_isolated(
        self,
        recipient: str,
        subject: str,
        body: str,
        html_body: str,
    ) -> bool:
        try:
            return await self.send_email(recipient, subject, body, html_body)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Isolated email notification failure for %s",
                self._redact_email(recipient),
            )
            return False

    async def _send_webhook_isolated(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        event_id: str,
        signature_secret: str | None,
    ) -> bool:
        try:
            if not event_id:
                # 保留 legacy 子类/测试替身覆盖 ``send_webhook(url, payload)`` 的兼容性。
                return await self.send_webhook(url, payload)
            return await self.send_webhook(
                url,
                payload,
                event_id=event_id,
                signature_secret=signature_secret,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Isolated webhook notification failure for %s", self._redact_url(url)
            )
            return False

    async def notify_alert(self, alert: dict[str, Any]) -> None:
        """保留 legacy 告警 API；仅补齐 HTML 转义与逐目标隔离。"""
        level = self._text(alert.get("level"), "info").lower()
        title = self._text(alert.get("title"), "Unknown Alert")
        description = self._text(alert.get("description"))
        timestamp = self._text(alert.get("timestamp"), "N/A")
        device = self._text(alert.get("device_id"), "N/A")

        if level not in ("critical", "warning"):
            return

        subject = self._header_safe(
            f"[SCN Alert][{level.upper()}] {title}",
            fallback="SCN Alert",
        )
        body = (
            f"Alert Level: {level}\n"
            f"Title: {title}\n"
            f"Description: {description}\n"
            f"Time: {timestamp}\n"
            f"Device: {device}"
        )
        heading_color = "#b91c1c" if level == "critical" else "#c2410c"
        heading_label = html.escape(level.upper(), quote=True)
        heading_title = html.escape(title, quote=True)
        html_body = f"""<!doctype html>
<html><body>
<h2 style="color: {heading_color}">[{heading_label}] {heading_title}</h2>
<p><strong>Description:</strong> {html.escape(description, quote=True)}</p>
<p><strong>Time:</strong> {html.escape(timestamp, quote=True)}</p>
<p><strong>Device:</strong> {html.escape(device, quote=True)}</p>
</body></html>"""

        if settings.SMTP_ENABLED and settings.ALERT_EMAIL_RECIPIENTS:
            await asyncio.gather(
                *(
                    self._send_email_isolated(recipient, subject, body, html_body)
                    for recipient in settings.ALERT_EMAIL_RECIPIENTS
                )
            )

        if settings.WEBHOOK_ENABLED and settings.ALERT_WEBHOOK_URLS:
            await asyncio.gather(
                *(
                    self._send_webhook_isolated(
                        url,
                        {
                            "alert": alert,
                            "level": level,
                            "title": title,
                            "timestamp": timestamp,
                        },
                        event_id="",
                        signature_secret=None,
                    )
                    for url in settings.ALERT_WEBHOOK_URLS
                )
            )

    @staticmethod
    def _format_target(alert: Mapping[str, Any]) -> str:
        target = alert.get("target")
        if isinstance(target, Mapping):
            target_type = NotificationService._text(target.get("type"), "target")
            target_key = NotificationService._text(
                target.get("stable_key") or target.get("id"), "unknown"
            )
            return f"{target_type}:{target_key}"
        return NotificationService._text(
            alert.get("target_stable_key") or alert.get("target_id"),
            "unknown",
        )

    @staticmethod
    def _format_measurement(value: Any) -> str:
        if not isinstance(value, Mapping):
            return "N/A"
        parts: list[str] = []
        depth = value.get("depth_m")
        fill_ratio = value.get("fill_ratio")
        if depth is not None:
            parts.append(f"depth_m={NotificationService._text(depth)}")
        if fill_ratio is not None:
            parts.append(f"fill_ratio={NotificationService._text(fill_ratio)}")
        return ", ".join(parts) if parts else "N/A"

    @staticmethod
    def _text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        text = str(value)
        return text if text else default

    @staticmethod
    def _header_safe(value: Any, *, fallback: str) -> str:
        text = NotificationService._text(value, fallback)
        sanitized = text.replace("\r", " ").replace("\n", " ").strip()
        return sanitized[:200] or fallback

    @staticmethod
    def _is_https_webhook(url: str) -> bool:
        try:
            parsed = urlsplit(url)
            if parsed.scheme.lower() != "https" or not parsed.hostname:
                return False
            if parsed.username or parsed.password or parsed.port not in (None, 443):
                return False
            hostname = parsed.hostname.rstrip(".").lower()
            if hostname == "localhost" or hostname.endswith(".localhost"):
                return False
            try:
                addresses = {ipaddress.ip_address(hostname)}
            except ValueError:
                try:
                    records = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
                    addresses = {
                        ipaddress.ip_address(record[4][0]) for record in records
                    }
                except (OSError, ValueError):
                    return False
            return bool(addresses) and all(
                not (
                    address.is_private
                    or address.is_loopback
                    or address.is_link_local
                    or address.is_multicast
                    or address.is_reserved
                    or address.is_unspecified
                )
                for address in addresses
            )
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _redact_url(url: str) -> str:
        try:
            parsed = urlsplit(url)
            if parsed.hostname:
                return f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
        except ValueError:
            pass
        return "<invalid-url>"

    @staticmethod
    def _redact_email(address: str) -> str:
        if "@" not in address:
            return "<invalid-email>"
        local, _domain = address.split("@", 1)
        return f"{local[:2]}***@***"


# 全局单例
notification_service = NotificationService()
