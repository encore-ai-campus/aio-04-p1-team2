"""승인된 Gmail 시험 주소에 실제 단계별 메일을 보내는 격리 통합 검증.

별도 API는 INQUIRY_WORKER_ENABLED=false로 실행한다. 시험 문의 전용 작업자만
가동하여 기존 고객의 보류 알림을 건드리지 않는다. 성공한 시험 문의·Jira는 보존한다.
"""
import argparse
from datetime import timedelta
from email.utils import parseaddr
import json
from pathlib import Path
import smtplib
import ssl
import time

import httpx
from sqlalchemy import select, text
from sqlmodel import Session

from backend.config import get_settings
from backend.core import jira, redis_store
from backend.core.timefmt import now
from backend.db import engine
from backend.inquiry_worker import start_worker
from backend.inquiry_workflow_models import InquiryNotification, InquirySyncState
from backend.models import User
from tools.migrate_sqlite_to_supabase import fingerprint

TARGET = "marinebaro12@gmail.com"


def run(base):
    settings = get_settings()
    stamp = now()
    output = Path(f"data/backups/smtp_inquiry_{stamp:%Y%m%d_%H%M%S}/result.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {"timestamp": stamp.isoformat(), "real_smtp": True, "checks": {}, "mailbox_receipt": "pending"}
    token, rid, stop, thread = "", "", None, None
    client = httpx.Client(base_url=base, timeout=45)
    stage = "preflight"

    def save():
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def request(method, path, admin=False, **kwargs):
        response = client.request(method, path, headers={"Authorization": "Bearer " + token} if admin else {}, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"http_{response.status_code}")
        return response.json()

    def old_notifications(excluded=""):
        schema = settings.database_schema
        with engine.connect() as connection:
            rows = connection.execute(text(f'SELECT * FROM "{schema}".inquiry_notification WHERE inquiry_id != :id ORDER BY id'), {"id": excluded}).mappings().all()
            return {row["id"]: fingerprint(dict(row)) for row in rows}

    def detail():
        return next(x for x in request("GET", "/admin/inquiries", True) if x["id"] == rid)

    def wait_sent(kind, status):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            item = detail()
            jobs = [job for job in item["notifications"] if job["kind"] == kind]
            if item["status"] == status and jobs and jobs[0]["status"] == "sent":
                print(f"verified: {kind} real SMTP sent", flush=True)
                return item
            if jobs and jobs[0]["status"] in ("failed", "unknown", "blocked"):
                raise RuntimeError("delivery_" + jobs[0]["status"] + "_" + jobs[0]["error_code"])
            time.sleep(2)
        raise RuntimeError("delivery_timeout_" + kind)

    def transition(category):
        options = jira._req("GET", f"/rest/api/3/issue/{key}/transitions")["transitions"]
        for option in options:
            dest = option["to"]
            cat = dest.get("statusCategory", {}).get("key")
            if not cat:
                cat = jira._req("GET", f"/rest/api/3/status/{dest['id']}")["statusCategory"]["key"]
            if cat == category:
                jira._req("POST", f"/rest/api/3/issue/{key}/transitions", json={"transition": {"id": option["id"]}})
                break
        else:
            raise RuntimeError("transition_missing_" + category)
        with Session(engine) as session:
            state = session.get(InquirySyncState, rid)
            state.poll_after = now() - timedelta(seconds=1)
            session.add(state)
            session.commit()

    try:
        assert settings.smtp_on and settings.inquiry_worker_enabled
        assert settings.smtp_user.strip().lower() == TARGET
        assert parseaddr(settings.mail_from or settings.smtp_user)[1].lower() == TARGET
        assert not settings.ops_email or settings.ops_email.strip().lower() == TARGET
        stage = "smtp_auth"
        kwargs = {"context": ssl.create_default_context()} if settings.smtp_port == 465 else {}
        factory = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
        with factory(settings.smtp_host, settings.smtp_port, timeout=15, **kwargs) as smtp:
            smtp.ehlo()
            if settings.smtp_port != 465:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            code, _ = smtp.login(settings.smtp_user, settings.smtp_password)
            result["checks"]["smtp_authenticated"] = code == 235
        baseline = old_notifications()
        stage = "api"
        health = request("GET", "/health")
        result["checks"]["api_db_redis_ready"] = health["db"] and health["redis"] and health["modes"]["mail"] == "smtp"
        with Session(engine) as session:
            admin = session.exec(select(User).where(User.role == "admin")).scalars().first()
            if not admin:
                raise RuntimeError("admin_missing")
            token = redis_store.session_create(admin.id)
        created = request("POST", "/inquiries", json={"type": "기능 건의", "title": f"[SMTP검증] 단계별 실제 메일 {stamp:%m-%d %H:%M}",
            "body": "승인된 시험용 문의입니다. 실제 접수·처리 시작·완료 메일의 발송과 수신을 확인합니다.", "email": TARGET})
        rid, key = created["id"], created["jira_key"]
        result.update(inquiry_id=rid, jira_key=key, ops_mail_reported_sent="운영팀 알림 메일을 발송했어요." in created["message"])
        save()
        if key or created.get("jira_status") != "not_registered":
            raise RuntimeError("unexpected_automatic_jira_creation")
        stop, thread = start_worker(engine, inquiry_id=rid)
        stage = "received"
        wait_sent("received", "접수")
        result["checks"]["receipt_sent"] = True
        save()
        stage = "started"
        reviewed = detail()
        linked = request("POST", f"/admin/inquiries/{rid}/jira/create", True,
                         json={"expected_version": reviewed["review_version"]})
        key = linked["inquiry"]["jira_key"]
        if not key:
            raise RuntimeError("admin_jira_creation_failed")
        result["jira_key"] = key
        transition("indeterminate")
        wait_sent("started", "처리 중")
        result["checks"]["jira_started_mail_sent"] = True
        save()
        request("PATCH", f"/admin/inquiries/{rid}", True, json={"answer": "운영팀입니다. 요청하신 사항의 검토와 처리를 완료했습니다. 이 답변은 실제 메일 검증을 위한 시험 안내입니다."})
        stage = "completed"
        transition("done")
        final = wait_sent("completed", "완료")
        result["checks"]["jira_completed_mail_sent"] = True
        result["history"] = final["history"]
        result["notifications"] = final["notifications"]
        result["checks"]["three_sent_once"] = len(final["notifications"]) == 3 and all(x["attempts"] == 1 and x["status"] == "sent" for x in final["notifications"])
        result["checks"]["completion_flag"] = final["mail_sent"]
        for _ in range(2):
            request("POST", f"/admin/inquiries/{rid}/jira/sync", True)
        time.sleep(6)
        repeated = detail()
        result["checks"]["repeated_sync_no_new_mail"] = repeated["notifications"] == final["notifications"]
        result["checks"]["existing_notifications_unchanged"] = old_notifications(rid) == baseline
        result["passed_smtp_and_jira"] = all(result["checks"].values())
    except Exception as exc:
        result["failure"] = {"stage": stage, "type": type(exc).__name__, "code": getattr(exc, "smtp_code", None)}
        # 외부 예외 본문에는 수신 주소·인증 정보가 포함될 수 있어 기록하지 않는다.
        if isinstance(exc, RuntimeError):
            result["failure"]["reason"] = str(exc)[:120]
        result["passed_smtp_and_jira"] = False
    finally:
        if stop:
            stop.set()
            thread.join(timeout=40)
        if token:
            redis_store.session_delete(token)
        client.close()
        save()
        print(json.dumps({"result_file": str(output), "passed": result.get("passed_smtp_and_jira"), "inquiry_id": rid, "failure": result.get("failure")}, ensure_ascii=False), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18005")
    parser.add_argument("--send", action="store_true", required=True, help="승인된 시험 주소에 실제 메일 발송")
    args = parser.parse_args()
    raise SystemExit(0 if run(args.base_url).get("passed_smtp_and_jira") else 1)
