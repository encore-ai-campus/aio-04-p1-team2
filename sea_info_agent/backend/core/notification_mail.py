"""고객 알림 전용 SMTP 전송. DATA 이후 결과 불명은 자동 재발송하지 않는다."""
from email.message import EmailMessage
from email.utils import parseaddr
import smtplib
import ssl

from backend.config import get_settings


class DeliveryError(Exception):
    def __init__(self, code: str, retryable=False, uncertain=False):
        self.code, self.retryable, self.uncertain = code, retryable, uncertain


def deliver(recipient: str, subject: str, body: str, notification_id: str):
    settings = get_settings()
    if not settings.smtp_on:
        raise DeliveryError("smtp_not_configured")
    sender = parseaddr(settings.mail_from or settings.smtp_user)[1]
    if "@" not in sender or "@" not in recipient or any(c in recipient for c in "\r\n"):
        raise DeliveryError("mail_address_invalid")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    message["Message-ID"] = f"<sea-info-{notification_id}@{sender.rsplit('@', 1)[1]}>"
    message.set_content(body)
    smtp, attempted = None, False
    try:
        context = ssl.create_default_context()
        if settings.smtp_port == 465:
            smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15, context=context)
        else:
            smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
            smtp.ehlo()
            smtp.starttls(context=context)
        smtp.ehlo()
        smtp.login(settings.smtp_user, settings.smtp_password)
        attempted = True
        refused = smtp.send_message(message)
        if refused:
            raise DeliveryError("recipient_rejected")
    except smtplib.SMTPAuthenticationError:
        raise DeliveryError("smtp_auth_failed") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
        raise DeliveryError("mail_address_rejected") from None
    except smtplib.SMTPResponseException as error:
        raise DeliveryError("smtp_rejected", retryable=400 <= error.smtp_code < 500) from None
    except (TimeoutError, OSError, smtplib.SMTPServerDisconnected):
        raise DeliveryError("delivery_uncertain" if attempted else "smtp_connection_failed",
                            retryable=not attempted, uncertain=attempted) from None
    except DeliveryError:
        raise
    except Exception:
        raise DeliveryError("delivery_uncertain" if attempted else "smtp_setup_failed", uncertain=attempted) from None
    finally:
        if smtp:
            try:
                smtp.quit()
            except Exception:
                try:
                    smtp.close()
                except Exception:
                    pass  # 전송 응답을 받은 뒤 연결 정리 실패가 발송 결과를 바꾸지 않는다.
