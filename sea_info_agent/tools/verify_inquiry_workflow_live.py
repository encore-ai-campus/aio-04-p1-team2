"""실제 API·Supabase·Jira 검증. SMTP는 미설정 시 모의 전송과 명확히 구분한다.

시험용 앱 사용자·문의만 생성하고 finally에서 정확한 ID로 정리한다.
실제 Jira 시험 이슈는 완료 상태로 남겨 추적 가능하게 한다.
"""
from datetime import timedelta
import json
from pathlib import Path
import time
from types import SimpleNamespace
import uuid
from unittest.mock import patch

import httpx
from sqlalchemy import delete, select, text
from sqlmodel import Session

from backend.config import get_settings
from backend.core import jira, redis_store
from backend.core.timefmt import now
from backend.db import engine
from backend.models import AdminLog, Inquiry, User
from backend.inquiry_workflow_models import InquiryEvent, InquiryNotification, InquirySyncState
from backend import inquiry_worker
from tools.migrate_inquiry_workflow import snapshot

BASE = "http://127.0.0.1:8000"
OUTPUT = Path("data/backups/inquiry_workflow_20261112/live.json")


def run():
    settings = get_settings()
    # 실제 발송은 별도 설정·수신 검증 단계에서 실시한다.
    if settings.smtp_on:
        raise RuntimeError("SMTP configured; review real delivery workflow before this mock-transport verifier")
    uid = "verify_notice_" + uuid.uuid4().hex[:12]
    rid = ""
    tokens = []
    result = {"timestamp": now().isoformat(), "checks": {}, "real_smtp": "not_run_smtp_not_configured"}
    client = httpx.Client(base_url=BASE, timeout=45)
    check = result["checks"]
    schema = settings.database_schema

    def request(method, path, token="", **kwargs):
        response = client.request(method, path, headers={"Authorization": "Bearer " + token} if token else {}, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code}: {method} {path}")
        return response.json()

    def item():
        return next(x for x in request("GET", "/me/inquiries", member) if x["id"] == rid)

    def wait_status(target):
        deadline = time.monotonic() + 85
        while time.monotonic() < deadline:
            found = item()
            if found["status"] == target:
                return found
            time.sleep(2)
        raise RuntimeError("worker_poll_timeout")

    def transition(category):
        options = jira._req("GET", f"/rest/api/3/issue/{key}/transitions")["transitions"]
        chosen = None
        for option in options:
            dest = option["to"]
            cat = dest.get("statusCategory", {}).get("key")
            if not cat:
                cat = jira._req("GET", f"/rest/api/3/status/{dest['id']}")["statusCategory"]["key"]
            if cat == category:
                chosen = option["id"]
                break
        if not chosen:
            raise RuntimeError("Jira_transition_missing_" + category)
        jira._req("POST", f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": chosen}})
        # 시험 문의의 다음 폴링만 앞당긴다. 실제 작업자에서 Jira를 조회한다.
        with Session(engine) as session:
            state = session.get(InquirySyncState, rid)
            state.poll_after = now() - timedelta(seconds=1)
            session.add(state)
            session.commit()

    try:
        health = request("GET", "/health")
        check["server_health"] = health["db"] and health["redis"] and health["database_backend"] == "postgresql"
        check["admin_unauthenticated_denied"] = client.get("/admin/inquiries").status_code == 401
        with engine.connect() as connection:
            before = snapshot(connection, schema, {"user", "inquiry", "adminlog"})
        with Session(engine) as session:
            admin = session.exec(select(User).where(User.role == "admin")).scalars().first()
            if not admin:
                raise RuntimeError("admin_missing")
            admin_token = redis_store.session_create(admin.id)
            tokens.append(admin_token)
            session.add(User(id=uid, nickname="알림검증전용", email="marinebaro12@gmail.com", role="user"))
            session.commit()
        member = redis_store.session_create(uid)
        tokens.append(member)
        check["member_admin_denied"] = client.get("/admin/inquiries", headers={"Authorization": "Bearer " + member}).status_code == 403
        made = request("POST", "/inquiries", member, json={"type": "기능 건의", "title": "[자동검증] 문의 이력과 알림 2026-11-12",
            "body": "시험 전용입니다. 접수·처리 시작·완료 및 알림 대기열을 검증합니다."})
        rid, key = made["id"], made["jira_key"]
        if key:
            raise RuntimeError("unexpected_automatic_jira_creation")
        reviewed = next(x for x in request("GET", "/admin/inquiries", admin_token) if x["id"] == rid)
        linked = request("POST", f"/admin/inquiries/{rid}/jira/create", admin_token,
                         json={"expected_version": reviewed["review_version"]})
        key = linked["inquiry"]["jira_key"]
        result.update(inquiry_id=rid, jira_key=key)
        if not key:
            raise RuntimeError("Jira_issue_not_created")
        check["receipt_and_jira_created"] = item()["history"][0]["kind"] == "received"
        denied = client.patch(f"/admin/inquiries/{rid}", headers={"Authorization": "Bearer " + admin_token}, json={"status": "완료"})
        check["jira_status_authority"] = denied.status_code == 409
        transition("indeterminate")
        started = wait_status("처리 중")
        check["real_jira_worker_started"] = any(x["kind"] == "started" for x in started["history"])
        print("live: Jira started -> DB history verified", flush=True)
        request("PATCH", f"/admin/inquiries/{rid}", admin_token, json={"answer": "자동 검증용 공개 답변입니다. 문의 처리가 완료되었습니다."})
        transition("done")
        done = wait_status("완료")
        check["real_jira_worker_completed"] = any(x["kind"] == "completed" for x in done["history"])
        print("live: Jira done -> DB history verified", flush=True)
        count = len(done["history"])
        for _ in range(2):
            request("POST", f"/admin/inquiries/{rid}/jira/sync", admin_token)
        check["repeat_sync_deduplicated"] = len(item()["history"]) == count
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            with Session(engine) as session:
                jobs = session.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id == rid).order_by(InquiryNotification.version)).scalars().all()
                if jobs[0].status == "blocked":
                    break
            time.sleep(1)
        check["three_mail_reservations"] = [x.kind for x in jobs] == ["received", "started", "completed"]
        check["smtp_missing_not_marked_sent"] = jobs[0].status == "blocked" and jobs[0].error_code == "smtp_not_configured" and all(x.sent_at is None for x in jobs)
        result["actual_queue_before_mock"] = [{"kind": x.kind, "status": x.status, "error_code": x.error_code} for x in jobs]
        deliveries = []
        def fake_transport(recipient, subject, body, notification_id):
            deliveries.append(notification_id)
        # 실제 PostgreSQL의 예약·잠금·발송 결과 기록 검증. 외부 SMTP는 호출하지 않는다.
        mock_settings = SimpleNamespace(smtp_on=True)
        with patch.object(inquiry_worker, "get_settings", return_value=mock_settings):
            for _ in range(3):
                assert inquiry_worker.dispatch_one(engine, fake_transport, inquiry_id=rid)
        with Session(engine) as session:
            jobs = session.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id == rid).order_by(InquiryNotification.version)).scalars().all()
            check["postgres_mock_delivery_order"] = deliveries == [x.id for x in jobs]
            check["postgres_mock_delivery_recorded"] = all(x.status == "sent" and x.attempts == 1 and x.sent_at for x in jobs) and session.get(Inquiry, rid).mail_sent
        check["public_response_hides_delivery_metadata"] = "notifications" not in item()
        admin_item = next(x for x in request("GET", "/admin/inquiries", admin_token) if x["id"] == rid)
        check["admin_delivery_history_visible"] = len(admin_item["notifications"]) == 3
        with Session(engine) as session:
            check["old_inquiries_no_notifications"] = not session.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id != rid)).first()
    except Exception as exc:
        result["error"] = type(exc).__name__ + ": " + str(exc)[:150]
        print("live verification failed: " + type(exc).__name__, flush=True)
    finally:
        for token in tokens:
            redis_store.session_delete(token)
        with engine.begin() as connection:
            if rid:
                connection.execute(delete(InquiryNotification).where(InquiryNotification.inquiry_id == rid))
                connection.execute(delete(InquiryEvent).where(InquiryEvent.inquiry_id == rid))
                connection.execute(delete(InquirySyncState).where(InquirySyncState.inquiry_id == rid))
                connection.execute(delete(AdminLog).where(AdminLog.target == rid))
                connection.execute(delete(Inquiry).where(Inquiry.id == rid, Inquiry.user_id == uid))
            connection.execute(delete(User).where(User.id == uid))
        with engine.connect() as connection:
            if 'before' in locals():
                check["existing_user_inquiry_adminlog_restored"] = before == snapshot(connection, schema, set(before))
        client.close()
        result["passed"] = all(check.values()) and "error" not in result
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(result,ensure_ascii=False), flush=True)
    return result


if __name__ == "__main__":
    raise SystemExit(0 if run()["passed"] else 1)
