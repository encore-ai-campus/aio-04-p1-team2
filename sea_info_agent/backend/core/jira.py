"""Jira 연동 (편성훈 뼈대 → 김바로 제보 라우터에서 사용).

Jira Cloud REST API v3 · 기본 인증(JIRA_EMAIL + JIRA_API_TOKEN). 키가 없으면 on() 이 False.
"""
from __future__ import annotations

import httpx

from backend.config import get_settings


class JiraError(Exception):
    def __init__(self, message: str, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


def on() -> bool:
    return get_settings().jira_on


def _adf(text: str) -> dict:
    """Jira v3 본문 형식(ADF)으로 바꿔요. 줄마다 한 문단."""
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": line}]} if line
                        else {"type": "paragraph", "content": []} for line in text.splitlines()]}


def _req(method: str, path: str, **kw) -> dict:
    s = get_settings()
    try:
        resp = httpx.request(method, s.jira_base_url.rstrip("/") + path, auth=(s.jira_email, s.jira_api_token),
                             headers={"Accept": "application/json"}, timeout=20, **kw)
    except httpx.HTTPError:
        raise JiraError("Jira 연결 결과를 확인하지 못했어요.", uncertain=method == "POST") from None
    if resp.status_code >= 400:
        raise JiraError("Jira 요청을 완료하지 못했어요.",
                        uncertain=method == "POST" and (resp.status_code >= 500 or resp.status_code == 408))
    return resp.json() if resp.content else {}


def create_issue(summary: str, description: str, kind: str, labels: list[str] | None = None) -> str:
    """이슈를 만들고 키(예: SEA-12)를 돌려줘요. kind: 'bug' | 'task'"""
    s = get_settings()
    issue_type = s.jira_issue_type_bug if kind == "bug" else s.jira_issue_type_default
    data = _req("POST", "/rest/api/3/issue", json={"fields": {
        "project": {"key": s.jira_project_key}, "summary": summary[:250], "issuetype": {"name": issue_type},
        "description": _adf(description), "labels": labels or ["sea-info"]}})
    return data["key"]


def issue_status(key: str) -> tuple[str, bool]:
    """(상태 이름, 끝났는지)"""
    data = _req("GET", f"/rest/api/3/issue/{key}", params={"fields": "status"})
    st = data["fields"]["status"]
    return st["name"], st.get("statusCategory", {}).get("key") == "done"


def issue_snapshot(key: str) -> dict:
    """진행 상태 및 변경시각을 조회한다. 내부 댓글은 고객 알림에 사용하지 않는다."""
    data = _req("GET", f"/rest/api/3/issue/{key}", params={"fields": "status,updated"})
    fields = data["fields"]
    status = fields["status"]
    return {"name": status["name"], "category": status.get("statusCategory", {}).get("key", ""),
            "updated": fields.get("updated", "")}


def browse_url(key: str) -> str:
    return f"{get_settings().jira_base_url.rstrip('/')}/browse/{key}" if key else ""


def inquiry_label(inquiry_id: str) -> str:
    return "sea-inquiry-" + inquiry_id.lower()


def find_inquiry_issues(inquiry_id: str) -> list[str]:
    """생성 응답 유실 후 같은 접수번호 이슈를 복구한다. 실패하면 생성하지 않는다."""
    label = inquiry_label(inquiry_id)
    import re
    project = get_settings().jira_project_key
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", project):
        raise JiraError("Jira 프로젝트 설정을 확인해 주세요.")
    data = _req("GET", "/rest/api/3/search/jql", params={"jql": f'project = "{project}" AND labels = "{label}"',
                "maxResults": 2, "fields": "key"})
    return [row["key"] for row in data.get("issues", [])]


def inquiry_issue_matches(key: str, inquiry_id: str) -> bool:
    """관리자가 지정한 기존 이슈의 접수번호를 검증한다. 타 문의 연결을 막는다."""
    import json
    data = _req("GET", f"/rest/api/3/issue/{key}", params={"fields": "labels,description,project"})
    fields = data["fields"]
    if fields.get("project", {}).get("key") != get_settings().jira_project_key:
        return False
    return (inquiry_label(inquiry_id) in fields.get("labels", [])
            or f"접수번호: {inquiry_id}" in json.dumps(fields.get("description"), ensure_ascii=False))
