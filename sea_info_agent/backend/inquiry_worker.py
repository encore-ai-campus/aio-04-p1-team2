"""DB 대기열을 사용하는 알림 작업자와 로컬에서도 동작하는 Jira 폴링."""
from datetime import timedelta
import logging
from threading import Event, Thread
import uuid

from sqlalchemy import or_, and_
from sqlmodel import Session, select

from backend.config import get_settings
from backend.core import jira
from backend.core.notification_mail import DeliveryError, deliver
from backend.core.timefmt import now
from backend.inquiry_workflow import locked_inquiry, sync_jira, recipient_for
from backend.inquiry_workflow_models import InquiryNotification, InquirySyncState
from backend.models import Inquiry

log = logging.getLogger("sea_info.inquiry_worker")
BLOCKING = ("pending", "sending", "blocked", "unknown", "waiting_answer")


def dispatch_one(engine, transport=None, inquiry_id=None):
    transport = transport or deliver
    stamp = now()
    with Session(engine) as session:
        expired_query = select(InquiryNotification).where(InquiryNotification.status == "sending",
            InquiryNotification.lease_until < stamp)
        if inquiry_id:
            expired_query = expired_query.where(InquiryNotification.inquiry_id == inquiry_id)
        expired = session.exec(expired_query.with_for_update(skip_locked=True)).all()
        for job in expired:
            job.status, job.error_code = "unknown", "worker_interrupted"
            session.add(job)
        session.commit()
        ready = get_settings().smtp_on
        eligible = [InquiryNotification.status == "pending"]
        if ready:
            eligible.append(and_(InquiryNotification.status == "blocked", InquiryNotification.error_code == "smtp_not_configured"))
        query = select(InquiryNotification).where(InquiryNotification.due_at <= stamp, or_(*eligible))
        if inquiry_id:
            query = query.where(InquiryNotification.inquiry_id == inquiry_id)
        jobs = session.exec(query.order_by(InquiryNotification.created_at, InquiryNotification.version)
                            .limit(50).with_for_update(skip_locked=True)).all()
        chosen = None
        for job in jobs:
            earlier = session.exec(select(InquiryNotification.id).where(InquiryNotification.inquiry_id == job.inquiry_id,
                InquiryNotification.version < job.version, InquiryNotification.status.in_(BLOCKING)).limit(1)).first()
            if earlier:
                continue
            inquiry = session.get(Inquiry, job.inquiry_id)
            recipient = recipient_for(inquiry, session) if inquiry else ""
            if not recipient or recipient != job.recipient:
                job.status, job.error_code = "blocked", "recipient_changed"
                session.add(job)
                continue
            if not get_settings().smtp_on:
                job.status, job.error_code = "blocked", "smtp_not_configured"
                session.add(job)
                continue
            chosen = job
            break
        if chosen is None:
            session.commit()
            return False
        chosen.status, chosen.claim, chosen.lease_until = "sending", uuid.uuid4().hex, stamp + timedelta(seconds=180)
        chosen.attempts += 1
        chosen.error_code = ""
        notification_id, claim, inquiry_id = chosen.id, chosen.claim, chosen.inquiry_id
        payload = chosen.recipient, chosen.subject, chosen.body, chosen.id
        session.add(chosen)
        session.commit()
    status, error, due = "sent", "", stamp
    try:
        transport(*payload)
    except DeliveryError as failure:
        error = failure.code
        if failure.uncertain:
            status = "unknown"
        elif failure.retryable:
            status = "pending"
        elif failure.code in ("smtp_not_configured", "smtp_auth_failed", "smtp_setup_failed"):
            status = "blocked"
        else:
            status = "failed"
    except Exception:
        status, error = "unknown", "delivery_uncertain"
    with Session(engine) as session:
        locked_inquiry(session, inquiry_id)
        job = session.exec(select(InquiryNotification).where(InquiryNotification.id == notification_id)
                           .with_for_update()).one()
        if job.status != "sending" or job.claim != claim:
            return False
        if status == "pending":
            if job.attempts >= 5:
                status = "failed"
            else:
                due = now() + timedelta(seconds=min(60 * (2 ** (job.attempts - 1)), 3600))
        job.status, job.error_code, job.due_at, job.lease_until = status, error, due, None
        if status == "sent":
            job.sent_at = now()
            if job.kind == "completed":
                latest = session.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id == job.inquiry_id,
                    InquiryNotification.kind == "completed").order_by(InquiryNotification.version.desc())).first()
                inquiry = session.get(Inquiry, job.inquiry_id)
                if inquiry and inquiry.status == "완료" and latest.id == job.id:
                    inquiry.mail_sent = True
                    session.add(inquiry)
        session.add(job)
        session.commit()
    return True


def poll_one(engine, inquiry_id=None):
    if not get_settings().jira_on:
        return False
    stamp = now()
    with Session(engine) as session:
        query = select(InquirySyncState).join(Inquiry, Inquiry.id == InquirySyncState.inquiry_id).where(
            Inquiry.jira_key != "", InquirySyncState.poll_after <= stamp,
            or_(InquirySyncState.poll_until == None, InquirySyncState.poll_until < stamp))  # noqa: E711
        if inquiry_id:
            query = query.where(InquirySyncState.inquiry_id == inquiry_id)
        state = session.exec(query.order_by(InquirySyncState.poll_after).limit(1)
                             .with_for_update(skip_locked=True, of=InquirySyncState)).first()
        if not state:
            return False
        inquiry = session.get(Inquiry, state.inquiry_id)
        inquiry_id, key = inquiry.id, inquiry.jira_key
        claim = uuid.uuid4().hex
        state.poll_claim, state.poll_until = claim, stamp + timedelta(seconds=90)
        state.poll_after = stamp + timedelta(seconds=max(15, get_settings().inquiry_poll_seconds))
        session.add(state)
        session.commit()
    try:
        snapshot = jira.issue_snapshot(key)
        with Session(engine) as session:
            inquiry = locked_inquiry(session, inquiry_id)
            state = session.get(InquirySyncState, inquiry_id)
            if state.poll_claim != claim:
                return False
            sync_jira(inquiry, snapshot, session)
            state.poll_until, state.poll_claim = None, ""
            session.add(state)
            session.commit()
    except Exception:
        log.warning("문의 Jira 동기화를 확인하지 못했습니다. 다음 주기에 재시도합니다.")
    return True


def start_worker(engine, inquiry_id=None):
    stop = Event()
    if not get_settings().inquiry_worker_enabled:
        return stop, None

    def run():
        while not stop.is_set():
            try:
                for _ in range(10):
                    if stop.is_set() or not dispatch_one(engine, inquiry_id=inquiry_id):
                        break
                for _ in range(5):
                    if stop.is_set() or not poll_one(engine, inquiry_id=inquiry_id):
                        break
            except Exception:
                log.warning("문의 알림 작업을 완료하지 못했습니다. 작업 상태를 확인해 주세요.")
            stop.wait(5)

    thread = Thread(target=run, name="inquiry-notifications", daemon=True)
    thread.start()
    return stop, thread
