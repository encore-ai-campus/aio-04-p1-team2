"""SMTP 경계에서 성공·안전 재시도·결과 불명을 구분한다."""
import smtplib
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from backend.core import notification_mail as mail


class SMTPTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(smtp_on=True, mail_from="Sea-Info <sender@example.test>",
            smtp_user="sender@example.test", smtp_password="test", smtp_host="smtp.example.test", smtp_port=587)
        config = patch.object(mail, "get_settings", return_value=self.settings)
        config.start()
        self.addCleanup(config.stop)
        self.smtp = Mock()
        self.smtp.send_message.return_value = {}
        factory = patch.object(mail.smtplib, "SMTP", return_value=self.smtp)
        factory.start()
        self.addCleanup(factory.stop)

    def send(self):
        mail.deliver("recipient@example.test", "subject", "body", "test-id")

    def test_success_has_stable_id_and_tls(self):
        self.send()
        self.smtp.starttls.assert_called_once()
        message = self.smtp.send_message.call_args.args[0]
        self.assertEqual(message['Message-ID'], '<sea-info-test-id@example.test>')

    def test_port465_uses_ssl_without_starttls(self):
        self.settings.smtp_port = 465
        with patch.object(mail.smtplib, 'SMTP_SSL', return_value=self.smtp) as factory:
            self.send()
            factory.assert_called_once()
        self.smtp.starttls.assert_not_called()
        self.smtp.login.assert_called_once()

    def test_ops_mail_port465_uses_ssl(self):
        from backend.core import mail as ops_mail
        self.settings.smtp_port = 465
        connection = Mock()
        connection.__enter__ = Mock(return_value=self.smtp)
        connection.__exit__ = Mock(return_value=False)
        with patch.object(ops_mail, 'get_settings', return_value=self.settings), \
             patch.object(ops_mail.smtplib, 'SMTP_SSL', return_value=connection) as factory:
            self.assertTrue(ops_mail.send('recipient@example.test','test','body')['sent'])
            factory.assert_called_once()
        self.smtp.starttls.assert_not_called()

    def test_ops_mail_port587_keeps_starttls(self):
        from backend.core import mail as ops_mail
        connection = Mock()
        connection.__enter__ = Mock(return_value=self.smtp)
        connection.__exit__ = Mock(return_value=False)
        with patch.object(ops_mail, 'get_settings', return_value=self.settings), \
             patch.object(ops_mail.smtplib, 'SMTP', return_value=connection):
            self.assertTrue(ops_mail.send('recipient@example.test','test','body')['sent'])
        self.smtp.starttls.assert_called_once()

    def test_cleanup_failure_does_not_lose_acknowledged_delivery(self):
        self.smtp.quit.side_effect = OSError()
        self.smtp.close.side_effect = OSError()
        self.send()

    def test_missing_configuration_does_not_send(self):
        self.settings.smtp_on = False
        with self.assertRaises(mail.DeliveryError) as caught:
            self.send()
        self.assertEqual(caught.exception.code, 'smtp_not_configured')
        self.smtp.send_message.assert_not_called()

    def test_auth_failure_is_blocked(self):
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535,b'auth')
        with self.assertRaises(mail.DeliveryError) as caught:
            self.send()
        self.assertEqual(caught.exception.code, 'smtp_auth_failed')
        self.assertFalse(caught.exception.retryable)

    def test_disconnect_before_data_can_retry(self):
        self.smtp.ehlo.side_effect = smtplib.SMTPServerDisconnected()
        with self.assertRaises(mail.DeliveryError) as caught:
            self.send()
        self.assertTrue(caught.exception.retryable)
        self.assertFalse(caught.exception.uncertain)

    def test_disconnect_during_data_requires_manual_check(self):
        self.smtp.send_message.side_effect = smtplib.SMTPServerDisconnected()
        with self.assertRaises(mail.DeliveryError) as caught:
            self.send()
        self.assertTrue(caught.exception.uncertain)
        self.assertFalse(caught.exception.retryable)

    def test_explicit_temporary_rejection_can_retry(self):
        self.smtp.send_message.side_effect = smtplib.SMTPDataError(451,b'temporary')
        with self.assertRaises(mail.DeliveryError) as caught:
            self.send()
        self.assertTrue(caught.exception.retryable)
