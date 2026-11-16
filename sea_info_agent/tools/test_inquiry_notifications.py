"""문의 알림·이력의 트랜잭션·권한·중복·장애 회귀. 실제 메일·Jira는 호출하지 않는다."""
import hashlib
import hmac
import json
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend import inquiry_workflow as flow, inquiry_worker
from backend.core import security
from backend.core.notification_mail import DeliveryError
from backend.core.timefmt import now
from backend.inquiry_workflow_models import InquiryEvent, InquiryNotification, InquirySyncState
from backend.models import Inquiry, User
from backend.routers import inquiries, inquiry_notifications


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)

        @event.listens_for(self.engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        SQLModel.metadata.create_all(self.engine)
        self.settings = SimpleNamespace(smtp_on=True, public_site_url="http://localhost:8501", ops_email="",
            jira_on=False, jira_base_url="https://jira.example.invalid", jira_webhook_secret="test-secret", is_local=True,
            inquiry_poll_seconds=60, inquiry_worker_enabled=False)
        for module in (flow, inquiries, inquiry_worker):
            patcher = patch.object(module, "get_settings", return_value=self.settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("gap_ok", True), ("lock_acquire", "test-lock"), ("lock_release", None)):
            patcher = patch.object(inquiries.redis_store, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(inquiries.jira, "on", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.users = {name: User(id=name, email=name + "@example.test", nickname=name,
                                role="admin" if name == "admin" else "user") for name in ("owner", "other", "admin")}
        with Session(self.engine, expire_on_commit=False) as session:
            session.add_all(list(self.users.values()))
            session.commit()
        app = FastAPI()
        app.include_router(inquiries.router)
        app.include_router(inquiry_notifications.router)

        def db():
            with Session(self.engine) as session:
                yield session

        def viewer(request: Request):
            return self.users.get(request.headers.get("x-test-user"))

        def member(request: Request):
            user = viewer(request)
            if not user:
                raise HTTPException(401)
            return user

        def admin(request: Request):
            user = member(request)
            if user.role != "admin":
                raise HTTPException(403)
            return user

        app.dependency_overrides[inquiries.get_session] = db
        app.dependency_overrides[security.optional_user] = viewer
        app.dependency_overrides[security.require_user] = member
        app.dependency_overrides[security.require_admin] = admin
        self.client = TestClient(app)
        self.transport = Mock()

    def create(self, guest=False):
        response = self.client.post("/inquiries", headers={} if guest else {"x-test-user": "owner"}, json={
            "type": "기능 건의", "title": "검증 문의", "body": "고객 문의 본문", "email": "guest@example.test"})
        self.assertEqual(response.status_code, 200)
        return response.json()["id"]

    def update(self, rid, status=None, answer=None, user="admin", reset_reason=None):
        body = {}
        if status is not None:
            body["status"] = status
        if answer is not None:
            body["answer"] = answer
        if reset_reason is not None:
            body["reset_reason"] = reset_reason
        return self.client.patch(f"/admin/inquiries/{rid}", headers={"x-test-user": user}, json=body)

    def jobs(self):
        with Session(self.engine) as session:
            return session.exec(select(InquiryNotification).order_by(InquiryNotification.version)).all()

    def events(self):
        with Session(self.engine) as session:
            return session.exec(select(InquiryEvent).order_by(InquiryEvent.version)).all()

    def dispatch(self):
        return inquiry_worker.dispatch_one(self.engine, self.transport)

    def test_receipt_is_atomic_with_history_and_pending_mail(self):
        self.create()
        self.assertEqual([event.kind for event in self.events()], ["received"])
        self.assertEqual([job.status for job in self.jobs()], ["pending"])
        self.assertEqual(self.jobs()[0].recipient, "owner@example.test")
        self.transport.assert_not_called()

    def test_editing_queued_completion_updates_one_reservation(self):
        rid = self.create()
        self.update(rid, status="완료", answer="첫 답변")
        self.update(rid, answer="수정 답변")
        self.update(rid, answer="최종 답변")
        completions = [job for job in self.jobs() if job.kind == "completed"]
        self.assertEqual(len(completions), 1)
        self.assertIn("최종 답변", completions[0].body)

    def test_scoped_dispatch_does_not_process_another_inquiry(self):
        first = self.create()
        second = self.create()
        self.assertTrue(inquiry_worker.dispatch_one(self.engine, self.transport, inquiry_id=second))
        self.assertEqual(len(self.transport.call_args_list), 1)
        with Session(self.engine) as session:
            first_job = session.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id == first)).one()
            self.assertEqual(first_job.status, "pending")

    def test_editing_uncertain_completion_does_not_schedule_duplicate(self):
        rid = self.create()
        self.update(rid, status="완료", answer="공개 답변")
        with Session(self.engine) as session:
            job = session.exec(select(InquiryNotification).where(InquiryNotification.kind == "completed")).one()
            job.status = "unknown"
            session.add(job)
            session.commit()
        self.update(rid, answer="수정 공개 답변")
        completions = [job for job in self.jobs() if job.kind == "completed"]
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0].status, "unknown")

    def test_rollback_removes_inquiry_event_and_queue(self):
        with Session(self.engine) as session:
            inquiry = Inquiry(id="rollback", type="기타", title="시험", body="시험", email="guest@example.test")
            session.add(inquiry)
            flow.received(inquiry, session)
            session.rollback()
        self.assertEqual(self.events(), [])
        self.assertEqual(self.jobs(), [])

    def test_three_stages_send_once_in_order(self):
        rid = self.create()
        self.assertEqual(self.update(rid, "처리 중", "확인하고 있어요").status_code, 200)
        self.assertEqual(self.update(rid, "완료", "공개 답변입니다.").status_code, 200)
        self.assertEqual([job.kind for job in self.jobs()], ["received", "reply", "completed"])
        for _ in range(3):
            self.assertTrue(self.dispatch())
        self.assertFalse(self.dispatch())
        self.assertEqual(self.transport.call_count, 3)
        self.assertEqual([job.status for job in self.jobs()], ["sent", "sent", "sent"])
        with Session(self.engine) as session:
            self.assertTrue(session.get(Inquiry, rid).mail_sent)

    def test_repeated_status_and_answer_do_not_duplicate(self):
        rid = self.create()
        for _ in range(3):
            self.update(rid, "처리 중", "확인하고 있어요")
        self.assertEqual(len(self.events()), 2)
        self.assertEqual(len(self.jobs()), 2)

    def test_completed_waits_for_public_answer(self):
        rid = self.linked()
        with Session(self.engine) as session:
            inquiry = flow.locked_inquiry(session, rid)
            flow.sync_jira(inquiry, self.snapshot("done"), session)
            session.commit()
        self.assertEqual(self.jobs()[-1].status, "waiting_answer")
        self.update(rid, "완료", "처리 결과입니다")
        self.assertEqual(len(self.jobs()), 2)
        self.assertEqual(self.jobs()[-1].status, "pending")
        self.assertIn("처리 결과입니다", self.jobs()[-1].body)

    def test_completed_answer_edit_after_send_sends_updated_answer(self):
        rid = self.create()
        self.update(rid, "완료", "결과")
        self.dispatch()
        self.dispatch()
        self.update(rid, "완료", "수정 결과")
        self.assertEqual(len(self.jobs()), 3)
        self.assertEqual(self.jobs()[-1].kind, "reply")
        self.assertIn("수정 결과", self.jobs()[-1].body)

    def test_reset_requires_reason_and_emails_reason_with_answer(self):
        rid = self.create()
        self.update(rid, "처리 중", "처리 중입니다")
        missing = self.update(rid, "접수", "다시 확인하겠습니다")
        self.assertEqual(missing.status_code, 422)
        changed = self.update(rid, "접수", "다시 확인하겠습니다", reset_reason="자료를 다시 확인해야 합니다")
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.jobs()[-1].kind, "reset")
        self.assertIn("자료를 다시 확인해야 합니다", self.jobs()[-1].body)
        self.assertIn("다시 확인하겠습니다", self.jobs()[-1].body)

    def test_admin_save_requires_answer(self):
        rid = self.create()
        self.assertEqual(self.update(rid, "처리 중").status_code, 422)
        self.assertEqual(self.update(rid, "처리 중", " ").status_code, 422)

    def test_reopen_cancels_waiting_completion(self):
        rid = self.linked()
        with Session(self.engine) as session:
            inquiry = flow.locked_inquiry(session, rid)
            flow.sync_jira(inquiry, self.snapshot("done"), session)
            session.commit()
            flow.sync_jira(inquiry, self.snapshot("indeterminate", offset=1), session)
            session.commit()
        self.assertEqual(self.jobs()[1].status, "cancelled")

    def test_smtp_missing_blocks_without_fake_success(self):
        self.create()
        self.settings.smtp_on = False
        self.assertFalse(self.dispatch())
        self.assertEqual(self.jobs()[0].status, "blocked")
        self.assertEqual(self.jobs()[0].attempts, 0)
        self.settings.smtp_on = True
        self.assertTrue(self.dispatch())
        self.assertEqual(self.jobs()[0].status, "sent")

    def test_safe_connection_failure_retries_later(self):
        self.create()
        self.transport.side_effect = DeliveryError("smtp_connection_failed", retryable=True)
        self.dispatch()
        self.assertEqual(self.jobs()[0].status, "pending")
        self.assertFalse(self.dispatch())

    def test_uncertain_result_does_not_retry_or_send_later_stage(self):
        rid = self.create()
        self.update(rid, "처리 중")
        self.transport.side_effect = DeliveryError("delivery_uncertain", uncertain=True)
        self.dispatch()
        self.assertEqual(self.jobs()[0].status, "unknown")
        self.assertFalse(self.dispatch())
        self.transport.assert_called_once()

    def test_interrupted_sending_becomes_unknown(self):
        self.create()
        with Session(self.engine) as session:
            job = session.exec(select(InquiryNotification)).one()
            job.status, job.lease_until = "sending", now() - timedelta(seconds=1)
            session.add(job)
            session.commit()
        self.assertFalse(self.dispatch())
        self.assertEqual(self.jobs()[0].status, "unknown")
        self.transport.assert_not_called()

    def test_unknown_retry_requires_explicit_non_delivery_confirmation(self):
        self.create()
        self.transport.side_effect = DeliveryError("delivery_uncertain", uncertain=True)
        self.dispatch()
        job = self.jobs()[0]
        path = f"/admin/inquiry-notifications/{job.id}/retry"
        self.assertEqual(self.client.post(path, headers={"x-test-user": "admin"}, json={}).status_code, 409)
        self.assertEqual(self.client.post(path, headers={"x-test-user": "admin"}, json={"confirmed_not_delivered": True}).status_code, 200)

    def test_notification_retry_admin_only(self):
        self.create()
        path = f"/admin/inquiry-notifications/{self.jobs()[0].id}/retry"
        self.assertEqual(self.client.post(path, json={}).status_code, 401)
        self.assertEqual(self.client.post(path, headers={"x-test-user": "owner"}, json={}).status_code, 403)

    def test_other_member_cannot_read_history(self):
        self.create()
        response = self.client.get("/me/inquiries", headers={"x-test-user": "other"})
        self.assertEqual(response.json(), [])
        response = self.client.get("/me/inquiries", headers={"x-test-user": "owner"})
        self.assertEqual(len(response.json()[0]["history"]), 1)
        self.assertNotIn("notifications", response.json()[0])

    def test_contact_removed_prevents_queued_delivery(self):
        rid = self.create()
        with Session(self.engine) as session:
            inquiry = session.get(Inquiry, rid)
            inquiry.user_id, inquiry.email = None, ""
            session.add(inquiry)
            session.commit()
        self.assertFalse(self.dispatch())
        self.assertEqual(self.jobs()[0].error_code, "recipient_changed")
        self.transport.assert_not_called()

    def test_local_mail_never_contains_localhost_link(self):
        self.create()
        self.assertNotIn("localhost", self.jobs()[0].body)

    def test_guest_receipt_uses_supplied_contact(self):
        self.create(guest=True)
        self.assertEqual(self.jobs()[0].recipient, "guest@example.test")

    def linked(self):
        rid = self.create()
        with Session(self.engine) as session:
            inquiry = session.get(Inquiry, rid)
            inquiry.jira_key = "TEST-1"
            session.add(inquiry)
            session.commit()
        return rid

    def test_linked_status_cannot_diverge_from_jira(self):
        rid = self.linked()
        self.assertEqual(self.update(rid, "처리 중").status_code, 409)
        self.assertEqual(self.update(rid, answer="고객에게 공개할 답변").status_code, 200)

    def snapshot(self, category="indeterminate", offset=0):
        return {"name": "시험 상태", "category": category, "updated": (now() + timedelta(seconds=offset)).isoformat()}

    def test_manual_sync_updates_progress_and_deduplicates(self):
        rid = self.linked()
        with patch.object(inquiries.jira, "on", return_value=True), patch.object(inquiries.jira, "issue_snapshot", return_value=self.snapshot()):
            for _ in range(2):
                response = self.client.post(f"/admin/inquiries/{rid}/jira/sync", headers={"x-test-user": "admin"})
                self.assertEqual(response.status_code, 200)
        self.assertEqual([event.kind for event in self.events()], ["received", "started"])

    def test_older_snapshot_does_not_revert_status(self):
        rid = self.linked()
        with Session(self.engine) as session:
            inquiry = flow.locked_inquiry(session, rid)
            flow.sync_jira(inquiry, self.snapshot("done", offset=10), session)
            session.commit()
            flow.sync_jira(inquiry, self.snapshot("indeterminate", offset=-10), session)
            session.commit()
            self.assertEqual(inquiry.status, "완료")

    def test_signed_webhook_replay_creates_single_transition(self):
        self.linked()
        body = json.dumps({"issue": {"key": "TEST-1"}}).encode()
        signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
        with patch.object(inquiries.jira, "issue_snapshot", return_value=self.snapshot()):
            for _ in range(2):
                response = self.client.post("/webhooks/jira", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature": signature})
                self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.events()), 2)

    def test_bad_webhook_signature_rejected(self):
        response = self.client.post("/webhooks/jira", json={"issue": {"key": "TEST-1"}}, headers={"X-Hub-Signature": "wrong"})
        self.assertEqual(response.status_code, 403)

    def test_legacy_url_secret_rejected_in_production(self):
        self.settings.is_local = False
        response = self.client.post("/webhooks/jira?secret=test-secret", json={})
        self.assertEqual(response.status_code, 403)

    def test_polling_updates_progress(self):
        rid = self.linked()
        self.settings.jira_on = True
        with patch.object(inquiry_worker.jira, "issue_snapshot", return_value=self.snapshot()):
            self.assertTrue(inquiry_worker.poll_one(self.engine))
            self.assertFalse(inquiry_worker.poll_one(self.engine))
        with Session(self.engine) as session:
            self.assertEqual(session.get(Inquiry, rid).status, "처리 중")

    def test_existing_baseline_does_not_receive_retroactive_mail(self):
        with Session(self.engine) as session:
            inquiry = Inquiry(id="legacy", type="기타", title="기존 문의", body="내용", email="old@example.test", jira_key="OLD-1")
            session.add(inquiry)
            session.flush()
            flow.state_for(inquiry, session)
            session.commit()
            flow.sync_jira(inquiry, self.snapshot("done", offset=-300), session)
            session.commit()
        self.assertEqual(self.jobs(), [])


if __name__ == "__main__":
    unittest.main()
