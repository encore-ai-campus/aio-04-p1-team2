"""A-06 문의 관리 (김바로 담당).

목록(접수번호 · 유형 · 제목 · 상태 · 날짜 · Jira)에서 한 줄을 고르면 아래에 내용 · 답변 · 상태 · Jira가 열려요.
저장하면 고객 화면(제보·건의의 처리 확인, 내 정보 › 내 문의)에 바로 보여요.
‘완료’ + 답변으로 저장하면 서버가 제보한 사람에게 결과 메일을 보내요 (한 번만).
Jira: 실패한 건은 [Jira 다시 보내기](이슈가 없을 때만), 등록된 건은 [Jira 상태 가져오기](끝났으면 완료로).

시안 캡처용: ?shot=detail (첫 번째 문의를 고른 상태)
            ?shot=jira_failed (Jira 등록에 실패한 문의를 고른 상태. 없으면 첫 문의를 실패 예시로 보여줘요)
"""
import streamlit as st

from components import auth_gate, ui
from services import auth
from services import inquiry_data as inq
from services.api import ApiError

admin = auth_gate.admin_only()

S = st.session_state
shot = st.query_params.get("shot")
STATUSES = inq.STATUSES
STATUS_KIND = {"접수": "", "처리 중": "warn", "완료": "ok"}
DEMO_ERROR = "Jira 오류 401: 인증 정보가 맞지 않아요 (시안 예시)"

if S.get("ainq_toast"):
    st.toast(S.pop("ainq_toast"))


def jira_connected() -> bool:
    try:
        return auth.health().get("modes", {}).get("jira") == "on"
    except ApiError:
        return False


def run(action, rid: str, ok_prefix: str = "") -> None:
    """버튼 동작 하나: 성공하면 알림, 실패하면 이 화면에 안내를 남기고 다시 그려요."""
    try:
        out = action(rid)
    except ApiError as e:
        S["ainq_flash"] = ("error", e.message)
    else:
        if out.get("ok", True):
            S["ainq_toast"] = ok_prefix + out["message"]
        else:
            S["ainq_flash"] = ("error", out["message"])
    st.rerun()


ui.page_header("문의 관리", "제보·건의에 답하고 처리 상태를 바꿔요.")

try:
    rows = inq.admin_list()
except ApiError as e:
    st.error(e.message, icon=":material/cloud_off:")
    st.stop()

with st.container(horizontal=True, gap="small"):
    for s in STATUSES:
        st.metric(s, f"{sum(1 for r in rows if r['status'] == s)}건", border=True)
    failed_n = sum(1 for r in rows if r.get("jira_status") == "failed")
    if failed_n:
        st.metric("Jira 실패", f"{failed_n}건", border=True)
    st.metric("검토 대기", f"{sum(1 for r in rows if r.get('handling_route', 'jira' if r.get('jira_key') else 'undecided') == 'undecided')}건", border=True)

c1, c2 = st.columns([3, 2], gap="medium")
with c1:
    status = st.segmented_control("상태", ["전체"] + STATUSES, default="전체", required=True, key="ainq_status")
with c2:
    kind = st.selectbox("유형", ["전체"] + inq.KINDS, key="ainq_kind")

shown = [r for r in rows if (status == "전체" or r["status"] == status) and (kind == "전체" or r["type"] == kind)]

# [BARO-SCOPE-EXTENSION] 주요 5개 파일 밖의 승인된 연관 수정.
# 변경 이유: inquiry_data.py의 조회 항목을 관리자 화면에서 쉽게 찾을 수 있도록 필터를 제공한다.
# [BARO-CHANGE-029] 전체 목록 기반 집계는 유지하며 검색·Jira 상태는 표시 목록에만 적용한다.
search = st.text_input("접수번호·제목·내용 검색", key="ainq_search").strip()
jira_filter = st.selectbox("Jira 상태", ["전체"] + list(inq.JIRA_ADMIN), key="ainq_jira_filter",
                           format_func=lambda v: inq.JIRA_ADMIN[v][0] if v in inq.JIRA_ADMIN else v)
shown = [r for r in shown if (not search or any(search.casefold() in str(r.get(k, "")).casefold()
                                              for k in ("id", "title", "body")))
         and (jira_filter == "전체" or r.get("jira_status") == jira_filter)]
routes = {"전체": "전체", "undecided": "검토 대기", "direct": "직접 처리", "jira": "Jira 처리"}
route_filter = st.selectbox("처리 방식", list(routes), format_func=routes.get, key="ainq_route_filter")
shown = [r for r in shown if route_filter == "전체" or r.get("handling_route", "jira" if r.get("jira_key") else "undecided") == route_filter]

if not shown:
    ui.empty_state("🐚", "조건에 맞는 문의가 없어요. 상태나 유형을 바꿔 보세요.")
    st.stop()

# 시안 캡처용: Jira 실패 상태 (진짜 실패 건이 없으면 첫 문의를 실패 예시로 보여줘요)
demo_failed = False
shot_row = None
if shot == "detail":
    shot_row = 0
elif shot == "jira_failed":
    shot_row = next((i for i, r in enumerate(shown) if r.get("jira_status") == "failed"), None)
    if shot_row is None:
        shot_row, demo_failed = 0, True
        shown[0] = {**shown[0], "jira_status": "failed", "jira_key": "", "jira_url": "", "jira_error": DEMO_ERROR}

table = [{"접수번호": r["id"], "유형": r["type"], "제목": r["title"], "상태": r["status"], "날짜": r["date"],
          "처리 방식": routes.get(r.get("handling_route", "jira" if r.get("jira_key") else "undecided")),
          "보낸 사람": r.get("sender", ""), "답변": "있음" if r.get("answer") else "없음",
          "Jira": r.get("jira_key") or inq.JIRA_ADMIN.get(r.get("jira_status", "off"), ("", ""))[0]} for r in shown]
default = {"selection": {"rows": [shot_row]}} if shot_row is not None else None
picked = st.dataframe(table, hide_index=True, on_select="rerun", selection_mode="single-row",
                      key=f"ainq_table_{status}_{kind}_{jira_filter}_{route_filter}_{search}", selection_default=default)
sel = list(picked.selection.rows) if picked else []
if not sel and shot_row is not None:  # 시안 캡처용: 줄을 고른 상태
    sel = [shot_row]
st.caption("한 줄을 고르면 아래에서 내용을 보고 답할 수 있어요. 스마트폰에서는 표를 옆으로 밀어 볼 수 있어요.")

if not sel:
    st.info("목록에서 문의를 골라 주세요.", icon=":material/touch_app:")
    st.stop()

item = shown[sel[0]]
rid = item["id"]
jst = item.get("jira_status", "off")
route = item.get("handling_route", "jira" if item.get("jira_key") else "direct" if item["status"] == "완료" else "undecided")
version = item.get("review_version", 0)
creation = item.get("jira_creation_state", "created" if item.get("jira_key") else "unknown" if jst == "failed" else "unregistered")
jira_label, jira_kind = inq.JIRA_ADMIN.get(jst, ("Jira 미연결", ""))

st.space("small")
ui.section(f"{rid} 처리하기")
flash = S.pop("ainq_flash", None)
if flash:
    st.error(flash[1], icon=":material/error:")
with st.container(border=True):
    with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center", gap="small"):
        st.markdown(f"**{item['title']}**", width="content")
        ui.chips(ui.chip(item["type"], "sea"), ui.chip(item["status"], STATUS_KIND.get(item["status"], "")),
                 ui.chip(jira_label, jira_kind))
    ui.muted(f"{item.get('sender', '')} · {item['date']} 접수")
    st.write(item["body"])
    from components.inquiry_history import render
    render(item)

    # ---------- 관리자 검토 ----------
    st.markdown(f"**처리 방식: {routes.get(route, route)}**")
    choices = ("직접 처리", "Jira 이슈 생성")
    selected = st.radio("처리할 방법", choices, horizontal=True,
                        index=1 if route == "jira" or item.get("jira_key") else 0,
                        key=f"ainq_method_{rid}", disabled=bool(item.get("jira_key")))
    if item.get("jira_key"):
        selected = "Jira 이슈 생성"
    note = st.text_area("관리자 내부 메모", value=item.get("internal_note", ""), max_chars=2000,
                        key=f"ainq_note_{rid}_{version}", help="고객 화면과 고객 메일에는 표시하지 않아요.")
    if not item.get("jira_key"):
        blocked = creation in ("creating", "unknown")
        if blocked and selected == "직접 처리":
            st.warning("Jira 등록 중이거나 결과 확인이 필요해요. 기존 이슈 확인 전에는 직접 처리로 변경할 수 없어요.")
        if selected == "직접 처리":
            if st.button("직접 처리하기" if route != "direct" else "검토 메모 저장", key=f"ainq_direct_{rid}", disabled=blocked):
                run(lambda r: inq.review(r, "direct", note, version), rid)
            if route != "direct":
                st.info("직접 처리하기를 눌러 처리 방식을 확정한 뒤 답변을 저장해 주세요.")
        else:
          with st.expander("Jira에 등록하기 · 기존 이슈 확인", expanded=True):
            summary = st.text_input("Jira 제목", value=item.get("jira_summary") or f"[{item['type']}] {item['title']}",
                                    max_chars=200, key=f"ainq_jira_summary_{rid}_{version}")
            description = st.text_area("Jira에 전달할 내용", value=item.get("jira_description") or item["body"],
                                       max_chars=5000, key=f"ainq_jira_description_{rid}_{version}")
            st.caption("등록할 때 접수번호·유형이 함께 전달돼요. 내부 메모는 Jira에 전달하지 않아요.")
            confirmed = False
            if creation in ("unknown", "creating"):
                confirmed = st.checkbox("Jira에서 해당 접수번호의 이슈가 생성되지 않은 것을 확인했어요", key=f"ainq_jira_checked_{rid}")
            submit = st.checkbox("전달 내용을 확인했으며 Jira에 등록할게요", key=f"ainq_jira_approve_{rid}")
            if st.button("Jira 등록 결과 확인·다시 시도" if creation in ("unknown", "creating") else "Jira 이슈 생성",
                         key=f"ainq_jira_create_{rid}", disabled=not submit or not jira_connected() or item["status"] == "완료"):
                run(lambda r: inq.jira_create(r, summary, description, note, version, confirmed), rid)
            if not jira_connected():
                st.caption("Jira 연결이 설정되지 않았어요. 직접 처리로 진행할 수 있어요.")
            if creation in ("unknown", "creating", "failed"):
                key = st.text_input("기존 Jira 이슈 번호", placeholder="예: SEA-123", key=f"ainq_jira_existing_{rid}")
                if st.button("기존 이슈 연결", key=f"ainq_jira_link_{rid}", disabled=not key.strip() or not jira_connected()):
                    run(lambda r: inq.jira_link(r, key, version), rid)

    # ---------- Jira ----------
    if item.get("jira_key") or (selected == "Jira 이슈 생성" and jst == "failed"):
      with st.container(border=True, gap="small"):
        if item.get("jira_key"):
            line = f"**Jira 이슈** · {item['jira_key']}" + (" · 끝남" if jst == "done" else "")
            if item.get("jira_url"):
                line = f"**Jira 이슈** · [{item['jira_key']}]({item['jira_url']})" + (" · 끝남" if jst == "done" else "")
            st.markdown(line)
            st.caption("Jira에서 이슈를 끝내면 [Jira 상태 가져오기]나 웹훅으로 이 문의가 ‘완료’가 돼요.")
        elif jst == "failed":
            st.markdown("**Jira 이슈** · 만들지 못했어요")
            st.error(item.get("jira_error") or "Jira에 보내지 못했어요.", icon=":material/sync_problem:")
            st.caption("제보는 저장돼 있어요. 재시도 전에 Jira에 같은 접수번호의 이슈가 있는지 확인해 주세요. "
                       "중복 방지 저장소에 장애가 있으면 전송을 보류해요.")
            if demo_failed:
                st.caption("시안 예시 상태예요. Jira가 연결되어 있지 않으면 다시 보내기는 안내만 보여요.")

    left, right = st.columns([3, 1], gap="medium")
    with left:
        answer = st.text_area("답변", value=item.get("answer", ""), height=120, key=f"ainq_answer_{rid}_{version}",
                              placeholder="고객에게 보일 답변을 적어 주세요. 해요체로 짧게 써요.")
    with right:
        new_status = st.selectbox("상태", STATUSES, index=STATUSES.index(item["status"]) if item["status"] in STATUSES else 0,
                                  key=f"ainq_state_{rid}_{version}", disabled=selected == "Jira 이슈 생성")
        if selected == "Jira 이슈 생성":
            st.caption("Jira 처리 중에는 상태를 직접 바꿀 수 없어요. 이슈 상태를 가져와 반영하며 답변은 여기서 적어요.")
        if not answer.strip():
            st.caption("처리 상태와 관계없이 답변을 저장하려면 문의자에게 보낼 내용을 적어 주세요.")
    reset_reason = ""
    if new_status == "접수" and item["status"] != "접수":
        reset_reason = st.text_area("접수로 되돌리는 사유 (필수)", max_chars=1000,
                                    key=f"ainq_reset_reason_{rid}_{version}",
                                    help="문의자 처리 이력에 표시되고 메일로도 전달돼요.")
    if not answer.strip():
        st.caption("답변은 필수예요. 저장하면 문의자에게 메일로 전달돼요.")
    with st.container(horizontal=True, gap="small"):
        disabled = (not answer.strip() or (new_status == "접수" and item["status"] != "접수" and not reset_reason.strip())
                    or (not item.get("jira_key") and (selected != "직접 처리" or route != "direct" or creation in ("creating", "unknown"))))
        if st.button("저장하기", type="primary", icon=":material/save:", key=f"ainq_save_{rid}", disabled=disabled):
            run(lambda r: inq.admin_save(r, new_status, answer.strip(), version, reset_reason.strip()), rid)
        if item.get("jira_key"):
            if st.button("Jira 상태 가져오기", icon=":material/cloud_download:", key=f"ainq_jira_sync_{rid}"):
                run(inq.jira_sync, rid)
    st.caption("새 답변과 접수로 되돌리는 사유는 문의자 처리 이력에 표시되고 메일 발송을 예약해요. "
               "같은 내용을 다시 저장하면 새 메일을 만들지 않아요. 발송 상태는 아래에서 확인할 수 있어요.")

    with st.expander("고객 메일 발송 이력 · 최근 50개"):
        labels = {"received": "접수", "started": "처리 시작", "completed": "완료",
                  "reply": "답변", "reset": "접수로 변경"}
        states = {"pending": "발송 대기", "sending": "발송 중", "sent": "발송 완료", "blocked": "설정·수신 주소 확인 필요",
                  "failed": "발송 실패", "unknown": "발송 결과 확인 필요", "waiting_answer": "공개 답변 대기", "cancelled": "취소"}
        for job in item.get("notifications", []):
            st.write(f"{labels.get(job['kind'], job['kind'])} · {states.get(job['status'], job['status'])} · 시도 {job['attempts']}회")
            st.caption(job["sent_at"] or job["created_at"])
            if job["status"] in ("blocked", "failed", "unknown"):
                confirmed = False
                if job["status"] == "unknown":
                    confirmed = st.checkbox("발신함을 확인했고 발송되지 않았어요", key=f"inq_mail_checked_{job['id']}")
                if st.button("메일 다시 예약", key=f"inq_mail_retry_{job['id']}", disabled=job["status"] == "unknown" and not confirmed):
                    run(lambda _: inq.retry_notification(job["id"], confirmed), rid)
        if not item.get("notifications"):
            st.caption("새 기능 적용 전의 알림은 이 대기열에 소급 생성하지 않아요.")
