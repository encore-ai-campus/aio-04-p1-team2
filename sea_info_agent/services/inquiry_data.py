"""제보·건의 도우미 (김바로 담당). 화면(views/report.py · views/admin/inquiries.py)이 FastAPI를 부를 때 써요.

    add(kind, title, body, email)        # 보내기 → {id, jira_status, jira_key}
    mine()                               # 회원: 내 문의 (GET /me/inquiries)
    lookup_send(rid, email)              # 비회원 처리 확인 ① 메일로 코드 → {sent, message, dev_code?}
    lookup_verify(rid, email, code)      # 비회원 처리 확인 ② 코드 확인 → 문의 한 건
    admin_list() / admin_save(...) / jira_retry(rid) / jira_sync(rid)   # A-06

실패하면 ApiError(상태, 한국어 문구)를 던져요 → 화면에서 st.error(e.message).
"""
from __future__ import annotations

from services import api, db
from services.api import ApiError

__all__ = ["ApiError"]

KINDS = ["오류 제보", "기능 건의", "생물 정보 정정", "기타"]
STATUSES = ["접수", "처리 중", "완료"]

# 고객 화면에 보여줄 Jira 단계 안내 (Jira는 운영팀이 일을 나눠 맡는 작업 목록이에요)
JIRA_NOTE = {
    "created": "운영팀이 확인한 뒤 담당 팀에 전달했어요.",
    "done": "담당 팀이 작업을 마쳤어요.",
    # [BARO-CHANGE-003] 응답 유실로 성공 여부가 불확실한 Jira 작업도 실패로 단정하지 않는다.
    "failed": "제보는 접수됐어요. 담당 팀 작업 목록(Jira)의 등록 여부는 운영팀이 확인해요.",
    "off": "운영팀이 확인한 뒤 담당 팀에 전달해요.",
    "not_registered": "문의가 접수되었어요. 운영팀이 내용을 확인하고 있어요.",
    "unknown": "문의는 접수됐어요. 운영팀이 처리 상황을 확인하고 있어요.",
}
# 관리자 화면 표시 (글자, 칩 종류)
JIRA_ADMIN = {"created": ("Jira 등록", "sea"), "done": ("Jira 완료", "ok"), "failed": ("Jira 실패", "danger"),
              "off": ("Jira 미등록", ""), "not_registered": ("Jira 미등록", ""),
              "unknown": ("Jira 결과 확인 필요", "warn")}


# ---------- 고객 ----------
def add(kind: str, title: str, body: str, email: str = "") -> dict:
    out = api.post("/inquiries", json={"type": kind, "title": title, "body": body, "email": email})
    db.clear_cache()
    return out


def mine() -> list[dict]:
    return db.inquiries()


def lookup_send(rid: str, email: str) -> dict:
    return api.post("/inquiries/lookup/send", json={"id": rid, "email": email})


def lookup_verify(rid: str, email: str, code: str) -> dict:
    return api.post("/inquiries/lookup/verify", json={"id": rid, "email": email, "code": code})


# ---------- 관리자 (A-06) ----------
# [BARO-CHANGE-004] 기존 호출과 호환되는 선택 인자로 서버 검색·Jira 필터를 전달한다.
def admin_list(status: str | None = None, kind: str | None = None,
               jira_status: str | None = None, q: str = "") -> list[dict]:
    params = {k: v for k, v in (("status", status), ("type", kind), ("jira_status", jira_status), ("q", q)) if v}
    return api.get("/admin/inquiries", params=params)


def admin_save(rid: str, status: str, answer: str, expected_version: int | None = None,
               reset_reason: str = "") -> dict:
    """답변 · 상태 저장. ‘완료’ + 답변이면 서버가 결과 메일까지 보내요. → {ok, message, inquiry}"""
    out = api.patch(f"/admin/inquiries/{rid}", json={"status": status, "answer": answer,
                                               "expected_version": expected_version,
                                               "reset_reason": reset_reason})
    db.clear_cache()
    return out


def jira_retry(rid: str) -> dict:
    """이슈가 없을 때만 다시 만들어요. → {ok, message, inquiry}"""
    out = api.post(f"/admin/inquiries/{rid}/jira/retry")
    db.clear_cache()
    return out


def jira_sync(rid: str) -> dict:
    """Jira 상태를 가져와요. 끝났으면 서버가 ‘완료’로 바꿔요. → {ok, message, inquiry, jira_state, done}"""
    out = api.post(f"/admin/inquiries/{rid}/jira/sync")
    db.clear_cache()
    return out


def retry_notification(notification_id: str, confirmed_not_delivered: bool = False) -> dict:
    return api.post(f"/admin/inquiry-notifications/{notification_id}/retry",
                    json={"confirmed_not_delivered": confirmed_not_delivered})


def review(rid: str, route: str, note: str, version: int) -> dict:
    out = api.post(f"/admin/inquiries/{rid}/review", json={"route": route, "internal_note": note, "expected_version": version})
    db.clear_cache()
    return out


def jira_create(rid: str, summary: str, description: str, note: str, version: int, confirmed: bool = False) -> dict:
    out = api.post(f"/admin/inquiries/{rid}/jira/create", json={"summary": summary, "description": description,
                   "internal_note": note, "expected_version": version, "confirmed_not_created": confirmed})
    db.clear_cache()
    return out


def jira_link(rid: str, issue_key: str, version: int) -> dict:
    out = api.post(f"/admin/inquiries/{rid}/jira/link", json={"issue_key": issue_key.strip().upper(), "expected_version": version})
    db.clear_cache()
    return out
