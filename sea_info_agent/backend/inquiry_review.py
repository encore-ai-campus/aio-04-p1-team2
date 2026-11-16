"""관리자 검토·처리 방식. 읽기 시 기존 문의를 변경하거나 메일을 생성하지 않는다."""
from datetime import timedelta
from fastapi import HTTPException
from sqlmodel import Session
from backend.core.timefmt import as_kst, now
from backend.inquiry_workflow_models import InquiryReview
from backend.models import AdminLog, Inquiry, User
from backend.core import jira, redis_store


def review_for(q: Inquiry, session: Session, persist: bool = False) -> InquiryReview:
    review = session.get(InquiryReview, q.id)
    if review is None:
        review = InquiryReview(inquiry_id=q.id,
            route="jira" if q.jira_key else "direct" if q.status == "완료" else "undecided",
            creation_state="created" if q.jira_key else "unknown" if q.jira_status == "failed" else "unregistered")
        if persist:
            session.add(review)
            session.flush()
    return review


def check_version(review: InquiryReview, expected: int | None):
    if expected is not None and review.version != expected:
        raise HTTPException(409, "다른 관리자가 문의를 변경했어요. 목록을 새로 고친 뒤 다시 선택해 주세요.")


def creation_busy(review: InquiryReview) -> bool:
    return (review.creation_state == "creating" and review.creation_started_at is not None
            and now() - as_kst(review.creation_started_at) < timedelta(seconds=120))


def ensure_direct_allowed(q: Inquiry, review: InquiryReview):
    if q.jira_key:
        raise HTTPException(409, "이미 Jira에 연결된 문의예요. Jira에서 처리 상태를 변경해 주세요.")
    if review.creation_state in ("creating", "unknown"):
        raise HTTPException(409, "Jira 등록 결과를 먼저 확인해 주세요. 확인 전에는 직접 처리로 바꿀 수 없어요.")


def record(review: InquiryReview, q: Inquiry, admin: User, action: str, note: str, session: Session):
    before = f"{review.route}/{review.creation_state}"
    review.reviewed_by, review.reviewed_at = admin.id, now()
    review.internal_note = note.strip()
    review.version += 1
    q.updated_at = now()
    session.add(review)
    session.add(q)
    session.add(AdminLog(screen="문의 관리", actor=admin.id, action=action, target=q.id,
                         before=before, after=f"{review.route}/{review.creation_state} · {review.internal_note}"))


def create_jira(inquiry_id: str, body, admin: User, session: Session) -> Inquiry:
    """관리자가 제출한 명시적 등록만 수행한다. 불명 결과는 자동 재생성하지 않는다."""
    from backend import inquiry_workflow as flow
    if not jira.on():
        raise HTTPException(409, "Jira 연결 설정이 필요해요. 문의는 사이트에서 계속 처리할 수 있어요.")
    lock = f"jira-create:{inquiry_id}"
    try:
        token = redis_store.lock_acquire(lock, 120)
    except Exception:
        raise HTTPException(503, "중복 방지 저장소에 연결하지 못했어요. Jira 등록을 보류했어요.") from None
    if not token:
        raise HTTPException(409, "이 문의의 Jira 등록을 처리 중이에요. 잠시 뒤 새로 고쳐 주세요.")
    try:
        q = flow.locked_inquiry(session, inquiry_id)
        review = review_for(q, session, True)
        if q.jira_key:
            return q
        check_version(review, body.expected_version)
        if q.status == "완료":
            raise HTTPException(409, "완료된 문의는 먼저 처리 중으로 다시 열고 검토해 주세요.")
        if creation_busy(review):
            raise HTTPException(409, "Jira 등록 결과를 기다리고 있어요. 잠시 뒤 확인해 주세요.")
        uncertain = review.creation_state in ("unknown", "creating")
        try:
            existing = jira.find_inquiry_issues(q.id)
        except Exception:
            raise HTTPException(502, "기존 Jira 이슈를 확인하지 못해 새 이슈를 만들지 않았어요.") from None
        if len(existing) > 1:
            raise HTTPException(409, "같은 접수번호의 Jira 이슈가 여러 개예요. 운영팀 확인이 필요해요.")
        if uncertain and not existing and not body.confirmed_not_created:
            if review.creation_state == "creating":
                review.creation_state = "unknown"
                review.version += 1
                session.add(review)
                session.commit()
            raise HTTPException(409, "이전 등록 결과를 확인해야 해요. 기존 이슈를 연결하거나 Jira에서 미등록을 확인해 주세요.")
        summary = body.summary.strip() or f"[{q.type}] {q.title}"
        description = body.description.strip() or q.body
        review.jira_summary, review.jira_description = summary, description
        review.route, review.creation_state = "jira", "creating"
        review.creation_started_at = now()
        record(review, q, admin, "Jira 등록 검토·요청", body.internal_note, session)
        session.commit()  # 외부 성공 후 장애가 나도 creating 상태가 남아 재생성을 막는다.
        try:
            key = existing[0] if existing else jira.create_issue(summary,
                f"접수번호: {inquiry_id}\n유형: {q.type}\n\n{description}",
                "bug" if q.type == "오류 제보" else "task", ["sea-info", jira.inquiry_label(inquiry_id)])
            import re
            if not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*", key):
                raise jira.JiraError("이슈 번호를 확인하지 못했어요.", uncertain=True)
        except Exception as exc:
            q = flow.locked_inquiry(session, inquiry_id)
            review = review_for(q, session, True)
            review.creation_state = "unknown" if getattr(exc, "uncertain", True) else "failed"
            q.jira_status = review.creation_state
            q.jira_error = "Jira 등록 결과를 확인해 주세요." if review.creation_state == "unknown" else "Jira 요청이 거절되었어요. 설정을 확인한 뒤 다시 시도해 주세요."
            record(review, q, admin, "Jira 등록 결과 기록", review.internal_note, session)
            session.commit()
            return q
        q = flow.locked_inquiry(session, inquiry_id)
        review = review_for(q, session, True)
        q.jira_key, q.jira_status, q.jira_error = key, "created", ""
        review.route, review.creation_state = "jira", "created"
        flow.change(q, "처리 중", None, "admin", session)
        record(review, q, admin, "기존 Jira 연결" if existing else "Jira 등록 완료", review.internal_note, session)
        session.commit()
        return q
    finally:
        try:
            redis_store.lock_release(lock, token)
        except Exception:
            pass
