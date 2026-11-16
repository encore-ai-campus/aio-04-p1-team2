"""제보·건의 API 모양 (pydantic, 김바로 담당).

화면이 받는 문의 한 건 모양은 시안 때 예시 데이터(mock/inquiries.json)와 같은 키를 써요:
id · type · title · body · status · date · user · email · answer  (+ jira_key · jira_status)
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

KINDS = ("오류 제보", "기능 건의", "생물 정보 정정", "기타")
STATUSES = ("접수", "처리 중", "완료")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def check_email(v: str) -> str:
    v = (v or "").strip().lower()
    if v and (len(v) > 120 or not _EMAIL.match(v)):
        raise ValueError("이메일 주소를 한 번 더 확인해 주세요.")
    return v


def _text(v: str, limit: int, empty: str, what: str) -> str:
    v = (v or "").strip()
    if not v:
        raise ValueError(empty)
    if len(v) > limit:
        raise ValueError(f"{what}은(는) {limit}자 안으로 적어 주세요.")
    return v


# ---------- 보내기 (U-16) ----------
class InquiryIn(BaseModel):
    type: str = Field(description=" · ".join(KINDS))
    title: str
    body: str
    email: str = Field("", description="비회원은 필수 (답변 받을 메일). 회원은 계정 메일을 써요.")

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in KINDS:
            raise ValueError("유형을 다시 골라 주세요. (오류 제보 · 기능 건의 · 생물 정보 정정 · 기타)")
        return v

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        return _text(v, 50, "제목을 적어 주세요.", "제목")

    @field_validator("body")
    @classmethod
    def _body(cls, v: str) -> str:
        return _text(v, 1000, "어떤 일인지 내용을 적어 주세요.", "내용")

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return check_email(v)


class InquiryCreated(BaseModel):
    id: str = Field(description="접수번호 (예: R-1108-K3F9)")
    jira_status: str = Field(description="not_registered(관리자 검토 대기) | created | failed")
    jira_key: str = ""
    # [BARO-SCOPE-EXTENSION] 주요 5개 파일 밖의 승인된 연관 수정.
    # 변경 이유: inquiries.py의 저장 성공·후속 연동 확인 불가를 report.py에 전달한다.
    # [BARO-CHANGE-022] 기존 응답에 선택적 안내를 추가하여 구버전 호출부와 호환한다.
    message: str = ""


class InquiryHistoryOut(BaseModel):
    id: str
    version: int
    kind: str
    status: str
    message: str
    created_at: str


class InquiryDeliveryOut(BaseModel):
    id: str
    kind: str
    status: str
    attempts: int
    error_code: str
    created_at: str
    sent_at: str = ""


class InquiryOut(BaseModel):
    """고객 화면용 (내 문의 · 처리 확인)."""
    id: str
    type: str
    title: str
    body: str
    status: str
    date: str  # YYYY-MM-DD
    user: str = ""
    email: str = ""
    answer: str = ""
    jira_key: str = ""
    jira_status: str = "off"
    updated: str = ""  # YYYY-MM-DD HH:MM
    history: list[InquiryHistoryOut] = Field(default_factory=list)


class InquiryAdminOut(InquiryOut):
    """관리자 화면용 (A-06). Jira 오류 내용과 링크, 답변 메일을 보냈는지까지 담아요."""
    sender: str = ""  # 닉네임 또는 '비회원 · 메일'
    jira_url: str = ""
    jira_error: str = ""
    mail_sent: bool = False
    notifications: list[InquiryDeliveryOut] = Field(default_factory=list)
    handling_route: str = "undecided"
    review_version: int = 0
    reviewed_by: str = ""
    reviewed_at: str = ""
    internal_note: str = ""
    jira_creation_state: str = "unregistered"
    jira_summary: str = ""
    jira_description: str = ""


# ---------- 비회원 처리 확인 (메일 코드 2단계) ----------
class LookupIn(BaseModel):
    id: str
    email: str

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        v = (v or "").strip().upper()
        if not v:
            raise ValueError("접수번호를 적어 주세요.")
        return v[:20]

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = check_email(v)
        if not v:
            raise ValueError("제보할 때 적은 이메일을 적어 주세요.")
        return v


class LookupSendOut(BaseModel):
    sent: bool = Field(description="실제 메일로 보냈는지 (개발 모드면 False)")
    message: str
    dev_code: Optional[str] = Field(None, description="로컬 · 메일 미연결일 때만 화면에 보여줄 코드")


class LookupVerifyIn(LookupIn):
    code: str

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        v = (v or "").strip()
        if not (len(v) == 6 and v.isdigit()):
            raise ValueError("메일로 받은 6자리 숫자를 적어 주세요.")
        return v


# ---------- 관리자 (A-06) ----------
class InquiryPatch(BaseModel):
    """고칠 칸만 보내요."""
    status: Optional[str] = None
    answer: Optional[str] = None
    reset_reason: Optional[str] = None
    expected_version: Optional[int] = Field(None, ge=0)

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is not None and v not in STATUSES:
            raise ValueError("상태는 접수 · 처리 중 · 완료 중에서 골라 주세요.")
        return v

    @field_validator("answer")
    @classmethod
    def _answer(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) > 1000:
            raise ValueError("답변은 1000자 안으로 적어 주세요.")
        return v

    @field_validator("reset_reason")
    @classmethod
    def _reset_reason(cls, v):
        if v is None:
            return v
        v = v.strip()
        if len(v) > 1000:
            raise ValueError("접수로 되돌리는 사유는 1000자 안으로 적어 주세요.")
        return v


class InquiryActionOut(BaseModel):
    ok: bool = True
    message: str
    inquiry: InquiryAdminOut


class JiraSyncOut(InquiryActionOut):
    jira_state: str = Field(description="Jira 상태 이름 (예: 진행 중, 완료)")
    done: bool


class InquiryReviewIn(BaseModel):
    route: Literal["direct", "undecided"]
    internal_note: str = Field("", max_length=2000)
    expected_version: int = Field(ge=0)


class InquiryJiraCreateIn(BaseModel):
    expected_version: int = Field(ge=0)
    summary: str = Field("", max_length=200)
    description: str = Field("", max_length=5000)
    internal_note: str = Field("", max_length=2000)
    confirmed_not_created: bool = False


class InquiryJiraLinkIn(BaseModel):
    expected_version: int = Field(ge=0)
    issue_key: str = Field(pattern=r"^[A-Z][A-Z0-9_]*-[1-9][0-9]*$")


# ---------- Jira 웹훅 (이슈가 끝나면 Jira가 불러요) ----------
class _JiraCategory(BaseModel):
    key: str = ""


class _JiraStatus(BaseModel):
    name: str = ""
    statusCategory: Optional[_JiraCategory] = None


class _JiraFields(BaseModel):
    status: Optional[_JiraStatus] = None


class _JiraIssue(BaseModel):
    key: str = ""
    fields: Optional[_JiraFields] = None


class JiraWebhookIn(BaseModel):
    """Jira가 보내는 내용 중 필요한 것만 읽어요. 나머지 칸은 무시해요."""
    model_config = ConfigDict(extra="ignore")
    webhookEvent: str = ""
    issue: Optional[_JiraIssue] = None

    def done(self) -> bool:
        st = self.issue.fields.status if self.issue and self.issue.fields else None
        return bool(st and st.statusCategory and st.statusCategory.key == "done")
