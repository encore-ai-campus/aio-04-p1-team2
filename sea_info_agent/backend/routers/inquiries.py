"""문의 접수 후 관리자 검토를 거쳐 직접 처리하거나 Jira에 등록한다."""
from __future__ import annotations

import secrets
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, select

from backend import inquiry_workflow as workflow
from backend import inquiry_review as review_flow
from backend.inquiry_workflow_models import InquiryReview
from backend.config import get_settings
from backend.core import jira, mail, redis_store
from backend.core.security import optional_user, require_admin, require_user, viewer_key
from backend.core.timefmt import as_kst, now
from backend.db import get_session
from backend.models import AdminLog, Inquiry, User
from backend.schemas.common import OkOut
from backend.schemas.inquiry import (InquiryActionOut, InquiryAdminOut, InquiryCreated, InquiryIn, InquiryOut,
                                     InquiryPatch, JiraSyncOut, JiraWebhookIn, LookupIn, LookupSendOut, LookupVerifyIn,
                                     InquiryReviewIn, InquiryJiraCreateIn, InquiryJiraLinkIn)

router = APIRouter(tags=["제보·건의"])

SCREEN = "문의 관리"
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 헷갈리는 0 · O · 1 · I 는 빼요
# Jira 라벨에는 띄어쓰기를 쓸 수 없어서 유형을 영문 라벨로 바꿔요
TYPE_LABEL = {"오류 제보": "bug-report", "기능 건의": "feature-request", "생물 정보 정정": "species-fix", "기타": "etc"}
SUBMIT_GAP = 20  # 같은 사람이 연달아 보내는 것 막기 (초)
LOOKUP_SENDS, LOOKUP_WINDOW = 5, 600  # 같은 접수번호 · 메일로 10분에 코드 5번까지


# ---------- 공통 도우미 ----------
def _new_id(session: Session) -> str:
    """추측하기 어려운 접수번호: R-월일-4자리(32글자 중에서 무작위)."""
    for _ in range(20):
        rid = f"R-{now():%m%d}-" + "".join(secrets.choice(_ALPHABET) for _ in range(4))
        if not session.get(Inquiry, rid):
            return rid
    raise HTTPException(500, "접수번호를 만들지 못했어요. 잠시 뒤 다시 보내 주세요.")


def _reply_to(q: Inquiry, session: Session) -> str:
    """답변 받을 메일. 회원이면 (예전 제보처럼 비어 있을 때) 계정 메일을 써요."""
    if q.email:
        return q.email
    u = session.get(User, q.user_id) if q.user_id else None
    return u.email if u else ""


def _out(q: Inquiry, session: Session | None = None) -> InquiryOut:
    return InquiryOut(id=q.id, type=q.type, title=q.title, body=q.body, status=q.status,
                      date=as_kst(q.created_at).strftime("%Y-%m-%d"), user=q.user_id or "", email=q.email,
                      answer=q.answer, jira_key=q.jira_key, jira_status=q.jira_status,
                      updated=as_kst(q.updated_at).strftime("%Y-%m-%d %H:%M"),
                      history=workflow.activity(q.id, session)[0] if session else [])


def _admin_out(q: Inquiry, session: Session) -> InquiryAdminOut:
    u = session.get(User, q.user_id) if q.user_id else None
    sender = u.nickname if u else f"비회원 · {q.email}"
    url = jira.browse_url(q.jira_key) if q.jira_key and get_settings().jira_base_url else ""
    # 과거 저장된 외부 오류 원문도 API 응답으로 내보내지 않아요.
    error = "Jira 연동을 완료하지 못했어요. 연결 설정을 확인하고 잠시 뒤 다시 시도해 주세요." if q.jira_error else ""
    history, notifications = workflow.activity(q.id, session)
    data = _out(q).model_dump()
    data["history"] = history
    review = review_flow.review_for(q, session)
    if not q.jira_key and review.creation_state == "unregistered":
        data["jira_status"] = "not_registered"
    return InquiryAdminOut(**data, sender=sender, jira_url=url, jira_error=error,
                           handling_route=review.route, review_version=review.version,
                           reviewed_by=review.reviewed_by,
                           reviewed_at=as_kst(review.reviewed_at).isoformat() if review.reviewed_at else "",
                           internal_note=review.internal_note, jira_creation_state=review.creation_state,
                           jira_summary=review.jira_summary, jira_description=review.jira_description,
                           mail_sent=q.mail_sent, notifications=notifications)


def _get(rid: str, session: Session) -> Inquiry:
    q = session.get(Inquiry, rid)
    if not q:
        raise HTTPException(404, "문의를 찾지 못했어요. 목록을 새로 고쳐 주세요.")
    return q


def _log(session: Session, actor: str, action: str, target: str, before: str = "", after: str = "") -> None:
    session.add(AdminLog(screen=SCREEN, actor=actor, action=action, target=target, before=before, after=after))


def _commit(session: Session, fail: str) -> None:
    try:
        session.commit()
    except Exception:
        session.rollback()
        print("[제보 저장 오류] 저장에 실패했어요.", flush=True)
        raise HTTPException(500, fail) from None


# ---------- Jira ----------




# [BARO-CHANGE-026] 운영 알림의 미설정·개발 처리·발송·확인 불가를 접수 결과와 구분한다.
def _notify_ops(q: Inquiry) -> str:
    s = get_settings()
    if not s.ops_email:
        return " 운영팀 알림 메일은 미설정 상태예요. 접수 내용은 문의 관리에 저장됐어요."
    jira_line = {"created": f"Jira 이슈: {q.jira_key}", "failed": "Jira 이슈: 등록 여부 확인이 필요해요",
                 }.get(q.jira_status, "Jira 이슈: 미등록 · 관리자 검토 대기")
    body = "\n".join([f"새 제보·건의가 들어왔어요.", "", f"접수번호: {q.id}", f"유형: {q.type}", f"제목: {q.title}",
                      f"연락 메일: {q.email or '(회원)'}", jira_line, "", q.body, "",
                      f"관리 화면: {s.public_site_url.rstrip('/')}/admin-inquiries"])
    try:
        result = mail.send(s.ops_email, f"[Sea-Info Agent] 새 제보 {q.id} · [{q.type}] {q.title}", body)
        if result.get("sent"):
            return " 운영팀 알림 메일을 발송했어요."
        return " 운영팀 알림 메일은 실제 발송되지 않았어요. 접수 내용은 문의 관리에 저장됐어요."
    except Exception:  # 알림 메일이 실패해도 제보는 이미 저장됐어요
        print("[담당 그룹 메일 오류] 알림 전송을 확인하지 못했어요.", flush=True)
        return " 운영팀 알림 메일의 발송 여부는 확인하지 못했어요. 접수 내용은 저장됐어요."


# ---------- 고객: 보내기 · 내 문의 · 비회원 처리 확인 ----------
@router.post("/inquiries", response_model=InquiryCreated, summary="제보·건의 접수 (관리자 검토 후 처리)")
def create_inquiry(body: InquiryIn, request: Request, user: User | None = Depends(optional_user),
                   session: Session = Depends(get_session)):
    email = user.email.lower() if user else body.email
    if not user and not email:
        raise HTTPException(422, "답변 받을 이메일을 적어 주세요. 로그인하면 이메일 없이 보낼 수 있어요.")
    try:
        if not redis_store.gap_ok(f"inquiry:{viewer_key(request, user)}", SUBMIT_GAP):
            raise HTTPException(429, f"조금 전에 제보를 보냈어요. {SUBMIT_GAP}초 뒤에 다시 보내 주세요.")
    except HTTPException:
        raise
    except Exception:  # Redis가 꺼져 있으면 도배 막기만 건너뛰어요
        pass
    q = Inquiry(id=_new_id(session), type=body.type, title=body.title, body=body.body,
                user_id=user.id if user else None, email=email, jira_status="not_registered")
    # [BARO-CHANGE-027] 최초 저장 이후 후속 연동 실패가 접수 실패로 바뀌지 않도록 응답을 별도 보관한다.
    rid = q.id
    session.add(q)
    workflow.received(q, session)
    session.add(InquiryReview(inquiry_id=rid))
    _commit(session, "보내지 못했어요. 적은 내용은 그대로 있어요. 다시 시도해 주세요.")  # ① 먼저 저장
    result = InquiryCreated(id=rid, jira_status="not_registered",
                            message="문의가 접수되었어요. 운영팀이 내용을 확인한 뒤 처리하고 안내해 드릴게요.")
    try:
        result.message += _notify_ops(q)  # ③ 알림의 실패도 이미 접수된 결과를 바꾸지 않는다.
    except Exception:
        session.rollback()
        result.message += " 운영팀 알림 결과는 확인하지 못했어요."
    return result


@router.get("/me/inquiries", response_model=list[InquiryOut], summary="내 제보·건의 (회원)")
def my_inquiries(user: User = Depends(require_user), session: Session = Depends(get_session)):
    rows = session.exec(select(Inquiry).where(Inquiry.user_id == user.id)).all()
    return [_out(q, session) for q in sorted(rows, key=lambda q: as_kst(q.created_at), reverse=True)]


def _match(body: LookupIn, session: Session) -> Inquiry:
    q = session.get(Inquiry, body.id)
    if not q or _reply_to(q, session).lower() != body.email:
        raise HTTPException(404, "일치하는 제보를 찾지 못했어요. 접수번호와 이메일을 한 번 더 확인해 주세요.")
    return q


def _mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return (name[:2] + "*" * max(len(name) - 2, 1)) + "@" + domain


@router.post("/inquiries/lookup/send", response_model=LookupSendOut, summary="비회원 처리 확인 ① 메일로 6자리 코드 받기")
def lookup_send(body: LookupIn, session: Session = Depends(get_session)):
    _match(body, session)
    key = f"{body.id}:{body.email}"
    # [BARO-CHANGE-028] 인증 저장소 장애는 조회 인증을 우회하지 않고 503으로 안내한다.
    try:
        r = redis_store.r()
        n = r.incr(f"lookupsend:{key}")
        if n == 1:
            r.expire(f"lookupsend:{key}", LOOKUP_WINDOW)
        if n > LOOKUP_SENDS:
            raise HTTPException(429, "코드를 너무 여러 번 받았어요. 10분 뒤에 다시 받아 주세요.")
        code = redis_store.code_create("lookup", key)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "처리 확인 인증을 지금 사용할 수 없어요. 잠시 뒤 다시 시도해 주세요.") from None
    try:
        res = mail.code_mail(body.email, code, "lookup")
    except Exception:
        print("[처리 확인 메일 오류] 메일 전송을 확인하지 못했어요.", flush=True)
        raise HTTPException(502, "메일을 보내지 못했어요. 잠시 뒤 다시 받아 주세요.") from None
    s = get_settings()
    dev = bool(res.get("dev") and not s.smtp_on and s.is_local)
    if not res.get("sent") and not dev:
        raise HTTPException(502, "확인 코드 메일이 발송되지 않았어요. 잠시 뒤 다시 받아 주세요.")
    return LookupSendOut(sent=bool(res.get("sent")), dev_code=code if dev else None,
                         message=("개발 모드예요. 실제 메일은 발송되지 않았어요. 화면의 코드를 입력해 주세요." if dev
                                  else f"{_mask(body.email)}로 6자리 코드를 보냈어요. 5분 안에 적어 주세요."))


@router.post("/inquiries/lookup/verify", response_model=InquiryOut, summary="비회원 처리 확인 ② 코드 확인 → 처리 상황")
def lookup_verify(body: LookupVerifyIn, session: Session = Depends(get_session)):
    q = _match(body, session)
    try:
        ok, _, msg = redis_store.code_check("lookup", f"{body.id}:{body.email}", body.code)
    except Exception:
        raise HTTPException(503, "처리 확인 인증을 지금 사용할 수 없어요. 잠시 뒤 다시 시도해 주세요.") from None
    if not ok:
        raise HTTPException(400, msg)
    return _out(q, session)


# ---------- 관리자 (A-06) ----------
@router.get("/admin/inquiries", response_model=list[InquiryAdminOut], summary="문의 목록 (관리자)")
def admin_list(status: str | None = None, type: str | None = None, jira_status: str | None = None, q: str = "",
               admin: User = Depends(require_admin), session: Session = Depends(get_session)):
    stmt = select(Inquiry)
    if status:
        stmt = stmt.where(Inquiry.status == status)
    if type:
        stmt = stmt.where(Inquiry.type == type)
    if jira_status:
        stmt = stmt.where(Inquiry.jira_status == jira_status)
    rows = [r for r in session.exec(stmt).all() if not q or q in r.title or q in r.id or q in r.body]
    rows.sort(key=lambda r: as_kst(r.created_at), reverse=True)
    return [_admin_out(r, session) for r in rows]


@router.patch("/admin/inquiries/{rid}", response_model=InquiryActionOut, summary="답변 · 상태 저장 (완료 + 답변이면 결과 메일)")
def admin_update(rid: str, body: InquiryPatch, admin: User = Depends(require_admin),
                 session: Session = Depends(get_session)):
    q = workflow.locked_inquiry(session, rid)
    review = review_flow.review_for(q, session, True)
    review_flow.check_version(review, body.expected_version)
    target = body.status or q.status
    if q.jira_key and target != q.status:
        raise HTTPException(409, "Jira에서 상태를 바꾼 뒤 상태 가져오기를 눌러 주세요. 공개 답변은 여기서 저장할 수 있어요.")
    if not (body.answer if body.answer is not None else q.answer).strip():
        raise HTTPException(422, "문의자에게 보낼 답변을 적어 주세요.")
    if q.status != "접수" and target == "접수" and not (body.reset_reason or "").strip():
        raise HTTPException(422, "접수로 되돌리는 사유를 적어 주세요.")
    if not q.jira_key:
        review_flow.ensure_direct_allowed(q, review)
        if target == "완료" and not (body.answer if body.answer is not None else q.answer).strip():
            raise HTTPException(422, "직접 처리를 완료하려면 사용자에게 안내할 답변을 적어 주세요.")
        review.route = "direct"
        if review.creation_state == "failed":
            review.creation_state, q.jira_status, q.jira_error = "unregistered", "not_registered", ""
    before = q.status
    changed = workflow.change(q, target, body.answer, "admin", session,
                              reset_reason=body.reset_reason or "")
    if changed:
        review_flow.record(review, q, admin, "문의 상태·공개 답변 저장", review.internal_note, session)
        _log(session, admin.nickname, "문의 상태·공개 답변 저장", q.id, before, q.status)
    _commit(session, "저장하지 못했어요. 잠시 뒤 다시 시도해 주세요.")
    return InquiryActionOut(message=f"{q.id} 저장했어요. 고객 안내는 알림 대기열에서 처리해요.", inquiry=_admin_out(q, session))


@router.post("/admin/inquiries/{rid}/jira/retry", response_model=InquiryActionOut, summary="Jira 다시 보내기 (이슈가 없을 때만)")
def admin_jira_retry(rid: str, admin: User = Depends(require_admin), session: Session = Depends(get_session)):
    q = workflow.locked_inquiry(session, rid)
    review = review_flow.review_for(q, session, True)
    if review.creation_state != "failed" or review.route != "jira":
        raise HTTPException(409, "관리자 검토 후 Jira 등록 화면을 사용해 주세요. 결과 불명 건은 기존 이슈 확인이 필요해요.")
    return admin_jira_create(rid, InquiryJiraCreateIn(expected_version=review.version,
        summary=review.jira_summary, description=review.jira_description, internal_note=review.internal_note), admin, session)


@router.post("/admin/inquiries/{rid}/review", response_model=InquiryActionOut)
def admin_review(rid: str, body: InquiryReviewIn, admin: User = Depends(require_admin), session: Session = Depends(get_session)):
    q = workflow.locked_inquiry(session, rid)
    review = review_flow.review_for(q, session, True)
    review_flow.check_version(review, body.expected_version)
    review_flow.ensure_direct_allowed(q, review)
    if q.status == "완료" and body.route == "undecided":
        raise HTTPException(409, "완료된 문의는 먼저 처리 중으로 다시 열어 주세요.")
    review.route = body.route
    if review.creation_state == "failed":
        review.creation_state, q.jira_status, q.jira_error = "unregistered", "not_registered", ""
    review_flow.record(review, q, admin, "직접 처리 선택" if body.route == "direct" else "검토 대기로 변경", body.internal_note, session)
    if body.route == "direct" and q.status == "접수":
        workflow.change(q, "처리 중", None, "admin", session)
    _commit(session, "검토 결과를 저장하지 못했어요.")
    return InquiryActionOut(message="관리자 검토 결과를 저장했어요.", inquiry=_admin_out(q, session))


@router.post("/admin/inquiries/{rid}/jira/create", response_model=InquiryActionOut)
def admin_jira_create(rid: str, body: InquiryJiraCreateIn, admin: User = Depends(require_admin), session: Session = Depends(get_session)):
    q = review_flow.create_jira(rid, body, admin, session)
    ok = bool(q.jira_key)
    return InquiryActionOut(ok=ok, message=f"Jira에 연결했어요 ({q.jira_key})." if ok else "Jira 등록 결과를 확인해 주세요.",
                            inquiry=_admin_out(q, session))


@router.post("/admin/inquiries/{rid}/jira/link", response_model=InquiryActionOut)
def admin_jira_link(rid: str, body: InquiryJiraLinkIn, admin: User = Depends(require_admin), session: Session = Depends(get_session)):
    q = workflow.locked_inquiry(session, rid)
    review = review_flow.review_for(q, session, True)
    review_flow.check_version(review, body.expected_version)
    if q.jira_key or review_flow.creation_busy(review) or q.status == "완료":
        raise HTTPException(409, "이미 연결됐거나 등록 중 또는 완료된 문의예요.")
    if not jira.on():
        raise HTTPException(409, "Jira 연결 설정이 필요해요.")
    try:
        matches = jira.inquiry_issue_matches(body.issue_key, rid)
    except Exception:
        raise HTTPException(502, "기존 Jira 이슈를 확인하지 못했어요.") from None
    if not matches:
        raise HTTPException(409, "이 Jira 이슈에 같은 접수번호가 있는지 확인해 주세요.")
    other = session.exec(select(Inquiry).where(Inquiry.jira_key == body.issue_key, Inquiry.id != rid)).first()
    if other:
        raise HTTPException(409, "다른 문의에 연결된 Jira 이슈예요.")
    q.jira_key, q.jira_status, q.jira_error = body.issue_key, "created", ""
    review.route, review.creation_state = "jira", "created"
    review_flow.record(review, q, admin, "기존 Jira 이슈 연결", review.internal_note, session)
    workflow.change(q, "처리 중", None, "admin", session)
    _commit(session, "기존 이슈 연결을 저장하지 못했어요.")
    return InquiryActionOut(message="기존 Jira 이슈를 연결했어요.", inquiry=_admin_out(q, session))


@router.post("/admin/inquiries/{rid}/jira/sync", response_model=JiraSyncOut, summary="Jira 상태 가져오기 (끝났으면 완료로)")
def admin_jira_sync(rid: str, admin: User = Depends(require_admin), session: Session = Depends(get_session)):
    q = _get(rid, session)
    if not q.jira_key or not jira.on():
        raise HTTPException(409, "연결된 Jira 이슈가 필요해요.")
    try:
        snapshot = jira.issue_snapshot(q.jira_key)
    except Exception:
        raise HTTPException(502, "Jira 상태를 가져오지 못했어요.") from None
    q = workflow.locked_inquiry(session, rid)
    changed = workflow.sync_jira(q, snapshot, session)
    if changed:
        _log(session, admin.nickname, "Jira 상태 동기화", q.id, after=q.status)
    _commit(session, "상태를 저장하지 못했어요.")
    return JiraSyncOut(message="Jira 상태와 공개 처리 이력을 확인했어요.", inquiry=_admin_out(q, session),
                       jira_state=snapshot["name"], done=snapshot["category"] == "done")


@router.post("/webhooks/jira", response_model=OkOut, summary="Jira 상태 변경 동기화")
async def jira_webhook(request: Request, payload: JiraWebhookIn, secret: str = Query(""),
                       session: Session = Depends(get_session)):
    expected = get_settings().jira_webhook_secret
    if not expected:
        raise HTTPException(403, "웹훅 비밀값이 설정되지 않았어요.")
    signature = request.headers.get("X-Hub-Signature", "")
    calculated = "sha256=" + hmac.new(expected.encode(), await request.body(), hashlib.sha256).hexdigest()
    signed = bool(signature and hmac.compare_digest(signature, calculated))
    local_legacy = bool(get_settings().is_local and secret and secrets.compare_digest(secret.encode(), expected.encode()))
    if not signed and not local_legacy:
        raise HTTPException(403, "웹훅 서명이 맞지 않아요.")
    key = payload.issue.key if payload.issue else ""
    q = session.exec(select(Inquiry).where(Inquiry.jira_key == key)).first() if key else None
    if not q:
        return OkOut(message="연결된 문의가 없는 이슈예요.")
    try:
        snapshot = jira.issue_snapshot(key)  # 재전송·역순 이벤트도 현재 Jira 상태를 기준으로 처리한다.
    except Exception:
        raise HTTPException(503, "Jira 상태 확인을 다시 시도해 주세요.") from None
    q = workflow.locked_inquiry(session, q.id)
    workflow.sync_jira(q, snapshot, session)
    _commit(session, "문의 상태를 저장하지 못했어요.")
    return OkOut(message="상태를 확인했어요. 필요한 고객 알림은 대기열에서 처리해요.")
