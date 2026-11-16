"""관리자 API 로그 모니터링."""
from datetime import datetime, timedelta, timezone

import streamlit as st

from components import auth_gate, ui
from services import api
from services.api import ApiError

auth_gate.admin_only()
KST = timezone(timedelta(hours=9))
today = datetime.now(KST).date()

ui.page_header("로그 모니터링", "API 서버의 현재 로그와 날짜별 보관 로그를 확인해요.")

# 2026.11.13 sunghoon 현재 로그와 달력으로 조회하는 과거 로그 화면 제공
mode = st.segmented_control("로그 선택", ["현재 로그", "과거 로그"], default="현재 로그", required=True)
selected = None
if mode == "과거 로그":
    # 2026.11.13 sunghoon 과거 로그는 어제까지만 선택하고 기존 세션의 당일 날짜도 보정
    latest = today - timedelta(days=1)
    current = min(st.session_state.get("admin_logs_date", latest), latest)
    # 2026.11.13 sunghoon 과거 로그 버튼 영역의 최대 너비와 버튼 높이를 통일
    st.markdown("""<style>
        .st-key-admin_log_controls {max-width: 600px;}
        .st-key-admin_log_controls .stButton button,
        .st-key-admin_log_controls .stFormSubmitButton button {min-height: 44px; height: 44px;}
    </style>""", unsafe_allow_html=True)
    with st.container(key="admin_log_controls"):
        previous, yesterday, following = st.columns(3, gap="small")
        if previous.button("이전 날짜", icon=":material/chevron_left:", width="stretch", key="admin_logs_previous"):
            current -= timedelta(days=1)
        if yesterday.button("어제", width="stretch", disabled=current == latest, key="admin_logs_yesterday"):
            current = latest
        if following.button("다음 날짜", icon=":material/chevron_right:", width="stretch",
                            disabled=current >= latest, key="admin_logs_next"):
            current += timedelta(days=1)
        st.session_state["admin_logs_date"] = current
        # 2026.11.13 sunghoon 날짜 입력과 조회 버튼을 아래쪽 기준으로 정렬
        with st.form("admin_log_date_form"):
            date_col, button_col = st.columns([3, 1], gap="small", vertical_alignment="bottom")
            chosen = date_col.date_input("조회할 날짜", value=current, max_value=latest,
                                         key=f"admin_log_date_{current.isoformat()}")
            if button_col.form_submit_button("조회", type="primary", icon=":material/search:", width="stretch"):
                st.session_state["admin_logs_date"] = chosen
                current = chosen
    selected = current.isoformat()
    st.caption(f"{selected} 보관 로그 · 직접 조회")
else:
    paused = st.toggle("자동 갱신 일시정지", value=False, key="admin_log_pause")
    st.caption("일시정지 중이에요." if paused else "5초마다 현재 로그를 갱신해요.")

count_col, search_col = st.columns([1, 3])
count = count_col.selectbox("조회 줄 수", [100, 200, 500], index=1)
keyword = search_col.text_input("검색", placeholder="예: ERROR, WARNING, Jira")


# 2026.11.13 sunghoon 검색한 줄 주변의 맥락도 함께 표시해 오류 추적이 끊기지 않도록 처리
def matching_context(lines: list[str], query: str) -> list[str]:
    if not query.strip():
        return lines
    indices = set()
    for i, line in enumerate(lines):
        if query.casefold().strip() in line.casefold():
            indices.update(range(max(0, i - 2), min(len(lines), i + 3)))
    out = []
    previous = -2
    for i in sorted(indices):
        if i > previous + 1 and out:
            out.append("⋯")
        out.append(lines[i])
        previous = i
    return out


# 2026.11.13 sunghoon 현재 로그만 주기적으로 갱신하고 과거 로그는 선택된 날짜 유지
@st.fragment(run_every=5 if mode == "현재 로그" and not paused else None)
def show_logs() -> None:
    params = {"lines": count}
    if selected:
        params["day"] = selected
    try:
        # 2026.11.13 sunghoon 작업 이력 API와 구분된 파일 로그 API 호출
        result = api.get("/admin/api-logs", params=params)
    except ApiError as e:
        if e.status == 404:
            st.info(e.message)
        else:
            st.error(e.message)
        return
    # 2026.11.13 sunghoon 서버가 이전 버전이거나 응답 형식이 다를 때 오류 안내
    if (not isinstance(result, dict) or not isinstance(result.get("lines"), list)
            or not isinstance(result.get("filename"), str) or not isinstance(result.get("truncated"), bool)):
        st.error("로그 응답이 올바르지 않아요. API 서버를 다시 시작한 뒤 확인해 주세요.")
        return
    lines = matching_context(result["lines"], keyword)
    st.caption(f"{result['filename']} · 최근 {len(result['lines'])}줄" +
               (" · 더 오래된 내용은 표시하지 않아요" if result["truncated"] else ""))
    if keyword.strip():
        st.caption("검색은 위에서 선택한 최근 로그 범위 안에서만 수행해요.")
    if not lines:
        st.info("검색 결과가 없어요." if keyword.strip() else "아직 기록된 로그가 없어요.")
        return
    # 2026.11.13 sunghoon 자동 갱신 중에는 최신 로그로 이동하고 일시정지하면 스크롤을 유지
    with st.container(height=500, border=True, autoscroll=(mode == "현재 로그" and not paused)):
        st.code("\n".join(lines), language=None, wrap_lines=True)


show_logs()
