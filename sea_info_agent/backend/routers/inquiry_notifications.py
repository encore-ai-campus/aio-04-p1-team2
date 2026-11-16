"""관리자 전용 메일 실패 재시도·불명 결과 확인. 자동 전송 자체는 작업자가 수행한다."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.core.security import require_admin
from backend.core.timefmt import now
from backend.db import get_session
from backend.inquiry_workflow import locked_inquiry, recipient_for, mail_content, activity
from backend.inquiry_workflow_models import InquiryNotification
from backend.models import AdminLog, User

router = APIRouter(prefix="/admin/inquiry-notifications", tags=["문의 알림 관리"])


class RetryIn(BaseModel):
    confirmed_not_delivered: bool = False


@router.post("/{notification_id}/retry")
def retry(notification_id: str, body: RetryIn, admin: User = Depends(require_admin), session: Session = Depends(get_session)):
    original = session.get(InquiryNotification, notification_id)
    if not original:
        raise HTTPException(404, "알림을 찾지 못했어요.")
    inquiry = locked_inquiry(session, original.inquiry_id)
    job = session.exec(select(InquiryNotification).where(InquiryNotification.id == notification_id)
                       .with_for_update().execution_options(populate_existing=True)).one()
    if job.status in ("sent", "sending", "cancelled"):
        raise HTTPException(409, "이미 발송했거나 처리 중 또는 취소된 알림이에요.")
    if job.status == "unknown" and not body.confirmed_not_delivered:
        raise HTTPException(409, "발신함을 확인해 미발송임을 확인한 뒤 다시 시도해 주세요.")
    if job.kind == "completed" and not inquiry.answer.strip():
        raise HTTPException(409, "공개 답변을 먼저 저장해 주세요.")
    recipient = recipient_for(inquiry, session)
    if not recipient:
        raise HTTPException(409, "수신 주소가 없어요.")
    job.recipient = recipient
    if job.kind not in ("reply", "reset"):
        job.subject, job.body = mail_content(inquiry, job.kind)
    job.status, job.error_code, job.due_at, job.attempts = "pending", "", now(), 0
    session.add(job)
    session.add(AdminLog(screen="문의 관리", actor=admin.nickname, action="고객 알림 재시도 예약", target=notification_id))
    session.commit()
    return {"ok": True, "message": "알림을 다시 예약했어요."}
