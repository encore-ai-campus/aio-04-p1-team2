"""문의 처리 이력·알림 대기열·Jira 동기화 상태. 기존 문의 테이블은 유지한다."""
from datetime import datetime
from sqlalchemy import Column, DateTime, UniqueConstraint
from sqlmodel import Field, SQLModel
from backend.core.timefmt import now


class InquiryReview(SQLModel, table=True):
    """관리자 전용 검토 정보. 고객 공개 이력·메일과 분리한다."""
    __tablename__ = "inquiry_review"
    inquiry_id: str = Field(primary_key=True, foreign_key="inquiry.id")
    route: str = "undecided"  # undecided | direct | jira
    version: int = 0
    reviewed_by: str = ""
    reviewed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    internal_note: str = ""
    creation_state: str = "unregistered"  # unregistered | creating | created | failed | unknown
    creation_started_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    jira_summary: str = ""
    jira_description: str = ""


class InquiryEvent(SQLModel, table=True):
    __tablename__ = "inquiry_event"
    __table_args__ = (UniqueConstraint("inquiry_id", "version"),)
    id: str = Field(primary_key=True)
    inquiry_id: str = Field(index=True, foreign_key="inquiry.id")
    version: int
    kind: str
    from_status: str = ""
    to_status: str
    message: str = ""
    source: str
    created_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))


class InquiryNotification(SQLModel, table=True):
    __tablename__ = "inquiry_notification"
    __table_args__ = (UniqueConstraint("event_id", "kind"),)
    id: str = Field(primary_key=True)
    inquiry_id: str = Field(index=True, foreign_key="inquiry.id")
    event_id: str = Field(foreign_key="inquiry_event.id")
    version: int
    kind: str
    recipient: str = ""
    subject: str
    body: str
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    error_code: str = ""
    claim: str = ""
    due_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))
    lease_until: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))
    sent_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))


class InquirySyncState(SQLModel, table=True):
    __tablename__ = "inquiry_sync_state"
    inquiry_id: str = Field(primary_key=True, foreign_key="inquiry.id")
    version: int = 0
    baseline_at: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))
    jira_updated_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    poll_after: datetime = Field(default_factory=now, sa_column=Column(DateTime(timezone=True), nullable=False))
    poll_claim: str = ""
    poll_until: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
