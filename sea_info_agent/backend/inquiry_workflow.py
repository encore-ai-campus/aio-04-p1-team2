"""동일 트랜잭션에 문의 상태·고객 공개 이력·알림 예약을 저장한다."""
from datetime import datetime
import uuid
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlmodel import Session, select

from backend.config import get_settings
from backend.core.timefmt import as_kst, now
from backend.models import Inquiry, User
from backend.inquiry_workflow_models import InquiryEvent, InquiryNotification, InquirySyncState

LABELS = {"received": "접수 확인", "started": "처리 시작", "completed": "처리 완료",
          "reply": "운영팀 답변", "reset": "문의 상태 변경"}


def locked_inquiry(session: Session, inquiry_id: str) -> Inquiry:
    query = select(Inquiry).where(Inquiry.id == inquiry_id).with_for_update().execution_options(populate_existing=True)
    inquiry = session.exec(query).first()
    if not inquiry:
        raise HTTPException(404, "문의를 찾지 못했어요.")
    return inquiry


def state_for(inquiry: Inquiry, session: Session) -> InquirySyncState:
    state = session.get(InquirySyncState, inquiry.id)
    if not state:
        state = InquirySyncState(inquiry_id=inquiry.id)
        session.add(state)
        session.flush()
    return state


def recipient_for(inquiry: Inquiry, session: Session) -> str:
    if inquiry.email:
        return inquiry.email.strip()
    user = session.get(User, inquiry.user_id) if inquiry.user_id else None
    return user.email.strip() if user else ""


def mail_content(inquiry: Inquiry, kind: str, detail: str = "") -> tuple[str, str]:
    label = LABELS[kind]
    message = {"received": "문의가 접수되었어요. 운영팀이 확인할게요.",
               "started": "운영팀이 문의 내용을 확인하고 처리를 시작했어요.",
               "completed": "문의 처리를 마쳤어요.\n\n운영팀 답변:\n" + inquiry.answer,
               "reply": detail,
               "reset": detail}[kind]
    body = f"안녕하세요, Sea-Info Agent예요.\n\n접수번호: {inquiry.id}\n제목: {inquiry.title}\n\n{message}\n"
    site = get_settings().public_site_url.rstrip("/")
    parsed = urlparse(site)
    if parsed.scheme == "https" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        body += f"\n처리 상황 확인: {site}/mypage?tab=inquiries\n" if inquiry.user_id else f"\n처리 상황 확인: {site}/report\n접수번호와 제보한 이메일로 확인해 주세요.\n"
    else:
        body += "\n서비스의 ‘내 문의’ 또는 ‘제보·건의 → 처리 확인’에서 접수번호로 확인할 수 있어요.\n"
    return f"[Sea-Info Agent] {label} ({inquiry.id})", body


def queue_mail(inquiry: Inquiry, event: InquiryEvent, kind: str, session: Session, detail: str = ""):
    recipient = recipient_for(inquiry, session)
    subject, body = mail_content(inquiry, kind, detail)
    status = "pending" if recipient else "blocked"
    error = "" if recipient else "recipient_missing"
    if kind == "completed" and not inquiry.answer.strip():
        status, error = "waiting_answer", "answer_required"
    notification = InquiryNotification(id=uuid.uuid4().hex, inquiry_id=inquiry.id, event_id=event.id,
        version=event.version, kind=kind, recipient=recipient, subject=subject, body=body, status=status, error_code=error)
    session.add(notification)
    return notification


def event_for(inquiry: Inquiry, kind: str, before: str, source: str, message: str, session: Session) -> InquiryEvent:
    state = state_for(inquiry, session)
    state.version += 1
    session.add(state)
    event = InquiryEvent(id=uuid.uuid4().hex, inquiry_id=inquiry.id, version=state.version, kind=kind,
                         from_status=before, to_status=inquiry.status, source=source, message=message)
    session.add(event)
    session.flush()
    return event


def received(inquiry: Inquiry, session: Session):
    session.flush()
    event = event_for(inquiry, "received", "", "user", "문의가 접수되었어요.", session)
    queue_mail(inquiry, event, "received", session)


def change(inquiry: Inquiry, target: str, answer: str | None, source: str, session: Session,
           notify: bool = True, reset_reason: str = ""):
    if target not in ("접수", "처리 중", "완료"):
        raise HTTPException(422, "문의 상태가 올바르지 않아요.")
    before, old_answer = inquiry.status, inquiry.answer
    answer_changed = answer is not None and answer != old_answer
    if before == target and not answer_changed:
        return False
    inquiry.status = target
    if answer is not None:
        inquiry.answer = answer
    inquiry.updated_at = now()
    session.add(inquiry)
    if before != target:
        kind = {"처리 중": "started", "완료": "completed"}.get(target, "status_changed")
        message = {"처리 중": "운영팀이 처리를 시작했어요.", "완료": "문의 처리를 마쳤어요.",
                   "접수": "문의 상태가 접수로 변경되었어요. 사유: " + reset_reason}[target]
        if answer_changed:
            message += "\n운영팀 답변: " + inquiry.answer
        event = event_for(inquiry, kind, before, source, message, session)
        if target != "완료":
            for job in session.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id == inquiry.id,
                                                                      InquiryNotification.status == "waiting_answer")).all():
                job.status, job.error_code = "cancelled", "superseded"
                session.add(job)
        if notify and kind in ("started", "completed"):
            if kind == "completed":
                inquiry.mail_sent = False
            if kind == "started" and answer_changed:
                queue_mail(inquiry, event, "reply", session, message)
            else:
                queue_mail(inquiry, event, kind, session)
        elif notify and target == "접수" and source == "admin":
            queue_mail(inquiry, event, "reset", session, message)
    elif answer_changed:
        event = event_for(inquiry, "reply_published", before, source, inquiry.answer or "공개 답변이 수정되었어요.", session)
        if target == "완료" and inquiry.answer.strip() and notify:
            latest = session.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id == inquiry.id,
                InquiryNotification.kind == "completed").order_by(InquiryNotification.version.desc())).first()
            if latest and latest.status in ("waiting_answer", "pending", "blocked", "failed"):
                latest.subject, latest.body = mail_content(inquiry, "completed")
                latest.recipient = recipient_for(inquiry, session)
                latest.status, latest.error_code = ("pending", "") if latest.recipient else ("blocked", "recipient_missing")
                session.add(latest)
            elif latest is None and not inquiry.mail_sent:
                queue_mail(inquiry, event, "completed", session)
            else:
                queue_mail(inquiry, event, "reply", session, "운영팀 답변:\n" + inquiry.answer)
        elif notify and source == "admin":
            queue_mail(inquiry, event, "reply", session, "운영팀 답변:\n" + inquiry.answer)
    return True


def sync_jira(inquiry: Inquiry, snapshot: dict, session: Session):
    if not inquiry.jira_key:
        return False
    state = state_for(inquiry, session)
    updated = datetime.fromisoformat(snapshot["updated"].replace("Z", "+00:00")) if snapshot.get("updated") else None
    if updated and state.jira_updated_at and as_kst(updated) < as_kst(state.jira_updated_at):
        return False
    category = snapshot.get("category")
    if category not in ("new", "indeterminate", "done"):
        raise ValueError("unknown_jira_category")
    target = {"new": "접수", "indeterminate": "처리 중", "done": "완료"}[category]
    from backend.inquiry_review import review_for
    review = review_for(inquiry, session)
    if category == "new" and review.reviewed_at:
        target = "처리 중"  # 검토 후 개발 대기인 문의를 '미검토 접수'로 되돌리지 않는다.
    notify = not (state.version == 0 and updated and as_kst(updated) < as_kst(state.baseline_at))
    changed = change(inquiry, target, None, "jira", session, notify=notify)
    if changed:
        review.version += 1
        session.add(review)
    inquiry.jira_status = "done" if category == "done" else "created"
    session.add(inquiry)
    if updated:
        state.jira_updated_at = updated
    session.add(state)
    return changed


def activity(inquiry_id: str, session: Session) -> tuple[list[dict], list[dict]]:
    events = session.exec(select(InquiryEvent).where(InquiryEvent.inquiry_id == inquiry_id).order_by(InquiryEvent.version.desc()).limit(50)).all()
    history = [{"id": event.id, "version": event.version, "kind": event.kind, "status": event.to_status,
                "message": event.message, "created_at": as_kst(event.created_at).isoformat(timespec="seconds")}
               for event in reversed(events)]
    notifications = session.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id == inquiry_id)
                                .order_by(InquiryNotification.version.desc()).limit(50)).all()
    deliveries = [{"id": job.id, "kind": job.kind, "status": job.status, "attempts": job.attempts,
                   "error_code": job.error_code, "created_at": as_kst(job.created_at).isoformat(timespec="seconds"),
                   "sent_at": as_kst(job.sent_at).isoformat(timespec="seconds") if job.sent_at else ""} for job in notifications]
    return history, deliveries
