"""U-10 글쓰기 (차유진 담당).

?board=<게시판> 이 기본 게시판이 되고, ?edit=<글 번호> 면 그 글을 고쳐요.
고른 게시판의 사용자 정의 항목(A-02에서 만든 것)을 fields.render 로 순서대로 보여줘요.
관찰 이야기는 생물 알아보기의 ‘공유하기’로만 올라가서 여기서는 고를 수 없어요.

저장할 때 서버가 다시 검사해요(권한 · 금칙어 · 연속 글쓰기 간격 · 추가 항목). 서버 안내는 그 칸 아래에 보여줘요.

시안 캡처용: ?shot=dive (다이빙 기록 · 추가 항목 2개) | errors (빠진 칸 안내, 서버 검사 결과) | cancel (그만두기 확인 팝업)
           ?edit=<글 번호> (글 고치기, 예: ?edit=p1&as=member · ?edit=p11&as=admin)
"""
import streamlit as st
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from components import fields, nav, ui
from components.community_responsive import load_responsive
from services import auth, db
from services import community_data as cd
from services.api import ApiError

S = st.session_state
# 2026.11.13 - yujin -"글쓰기 전용 모바일·태블릿 배치를 불러오고 공통 스타일 파일은 유지해요."
load_responsive("write")
user = auth.current_user()
FP = "write_f"  # 사용자 정의 항목 위젯 key 앞부분
TEXT_KEYS = ("write_title", "write_body", "write_consent", "write_photo_id", "write_visibility")
# 2026.11.12 - yujin -"사용자별 임시저장 글은 최대 5개까지 보관해요."
MAX_SAVED_DRAFTS = 5
# 2026.11.13 - yujin -"모임 모집 글의 고정 문구를 입력 화면과 게시글 저장에서 동일하게 사용해요."
RECRUITMENT_NOTICE = "함께하실 분은 댓글로 참여 의사를 남겨주세요!"
# 2026.11.13 - yujin -"모임 모집의 항목은 직접 채울 수 있는 본문 양식으로 제공하고 마지막 참여 안내만 고정해요."
RECRUITMENT_TEMPLATE = "● 활동 내용 :\n● 모임 날짜와 시간 :\n● 집결 장소:\n● 예상 소요 시간 :\n● 준비물 :"


def fixed_body_notice(b: dict, extra: dict) -> str:
    # 2026.11.13 - yujin -"바다지킴이의 활성 단일 선택 항목에서 모임 모집을 골랐을 때만 본문 고정 문구를 적용해요."
    definition = next((f for f in fields.active(b.get("fields"))
                       if f["key"] == "writetype" and f.get("type") == "single"), None)
    selected = extra.get("writetype")
    if (b["id"] == "environment" and definition and isinstance(selected, str)
            and selected in (definition.get("options") or []) and "".join(selected.split()) == "모임모집"):
        return RECRUITMENT_NOTICE
    return ""


def editable_body(b: dict, body: str, extra: dict) -> str:
    # 2026.11.13 - yujin -"수정 글의 끝에 저장된 고정 문구를 입력 부분에서 분리해 재저장 시 중복되지 않게 해요."
    notice = fixed_body_notice(b, extra)
    if notice:
        while body.rstrip().endswith(notice):
            body = body.rstrip()[:-len(notice)].rstrip()
    return body


# 2026.11.13 - yujin -"바다지킴이 작성 유형에 따라 제목·본문 안내만 바꾸고 다른 게시판의 안내는 유지해요."
def editor_placeholders(b: dict) -> tuple[str, str]:
    default = ("무엇에 대한 글인가요?",
               "- 사람 얼굴이나 차 번호판이 사진에 나오지 않게 해 주세요.\n"
               "- 사는 곳은 지역까지만 적어요. 자세한 주소는 쓰지 않아요.\n"
               "- 생물을 만났다면 사진만 찍고 제자리에 두어요.")
    definition = next((f for f in fields.active(b.get("fields"))
                       if f["key"] == "writetype" and f.get("type") == "single"), None)
    if b["id"] != "environment" or not definition:
        return default
    selected = S.get(f"{FP}_writetype")
    if selected not in (definition.get("options") or []):
        return default
    # 2026.11.13 - yujin -"관리자에 저장된 '실천 공유'·'모임 모집'의 띄어쓰기를 안내 비교에서만 무시하고 저장 값은 보존해요."
    selected = "".join(selected.split()) if isinstance(selected, str) else ""
    return {
        "실천공유": ("환경을 위해 어떤 실천을 했나요?",
                 "어디서, 언제, 어떤 활동을 했는지 자유롭게 알려주세요.\n"
                 "활동하며 느낀 점이나 함께 나누고 싶은 팁도 좋아요."),
        "모임모집": ("함께할 환경 활동을 소개해주세요.",
                 "참여할 분들이 찾아올 수 있도록 아래 내용을 적어주세요."),
    }.get(selected, default)

# 콜백에서 저장을 마쳤으면 새 글로 이동
if S.get("write_new_post"):
    nav.go("community", post=S.pop("write_new_post"))
if S.get("write_cancel_go"):
    nav.go("community", **S.pop("write_cancel_go"))

edit_id = st.query_params.get("edit")
qp_board = st.query_params.get("board")


def photo_key() -> str:
    return f"write_photo_{S.get('_write_photo_version', 0)}"


def selected_photo():
    return S.get(photo_key())


# 2026.11.12 - yujin -"위젯과 별도로 사용자·새 글/수정 글별 제목과 본문을 세션에 임시 저장해요."
def save_draft() -> None:
    # 2026.11.13 - yujin -"자동 모집 양식을 직접 수정했다면 자동 입력 표시를 해제해 유형 전환으로 내용이 지워지지 않게 해요."
    if S.get("_write_auto_recruitment_template") and S.get("write_body") != RECRUITMENT_TEMPLATE:
        S.pop("_write_auto_recruitment_template", None)
    context = S.get("_write_draft_context")
    if context:
        S.setdefault("_write_drafts", {})[context] = {
            "title": S.get("write_title") or "", "body": S.get("write_body") or "",
            "board": S.get("write_board"),
            # 2026.11.12 - yujin -"임시 보관한 글을 다시 열 때 선택했던 공개 범위도 복원해요."
            "visibility": S.get("write_visibility", "public"),
            "manual_draft_id": S.get("_write_manual_draft_id"),
        }


def reset(discard_draft: bool = True) -> None:
    # 2026.11.12 - yujin -"게시 성공·명시적 취소 때만 현재 초안을 지우고 화면 전환 시 다른 초안은 보존해요."
    if discard_draft:
        S.get("_write_drafts", {}).pop(S.get("_write_draft_context"), None)
    S.pop("_write_draft_context", None)
    # 2026.11.12 - yujin -"편집창을 초기화해도 사용자가 명시적으로 저장한 초안 목록은 보존해요."
    S.pop("_write_manual_draft_id", None)
    # 2026.11.12 - yujin -"입력값 초기화 시 수정 글 로딩 상태도 지워 같은 글을 다시 불러오게 해요."
    S.pop("_write_edit_loaded", None)
    # 2026.11.13 - yujin -"새 작성창에서는 모집 양식을 다시 제공할 수 있도록 유형 전환 기록을 초기화해요."
    S.pop("_write_body_mode", None)
    # 2026.11.13 - yujin -"글 전환·초안 복원 시 이전 작성창의 자동 양식 표시를 남기지 않아요."
    S.pop("_write_auto_recruitment_template", None)
    # 업로더는 값을 직접 비울 수 없어 새 key로 다시 만들어요.
    S["_write_photo_version"] = S.get("_write_photo_version", 0) + 1
    for k in TEXT_KEYS + ("write_errors", "write_cancel"):
        S.pop(k, None)
    for b in db.boards(active_only=False):
        fields.clear(b.get("fields"), FP)


# 2026.11.12 - yujin -"임시저장 버튼을 누를 때마다 별도 초안을 추가하고 편집 화면과 입력값은 유지해요."
def save_manual_draft(bid: str, pid: str | None) -> None:
    # 2026.11.12 - yujin -"5개가 차면 기존 글과 입력값을 보존하고 추가 임시저장을 차단해요."
    saved = S.get("_write_saved_drafts", {}).get(user["id"], [])
    if len(saved) >= MAX_SAVED_DRAFTS:
        S["write_draft_notice"] = f"임시저장한 글은 최대 {MAX_SAVED_DRAFTS}개까지 보관할 수 있어요."
        return
    title, body = S.get("write_title") or "", S.get("write_body") or ""
    if not title.strip() and not body.strip():
        S["write_draft_notice"] = "제목이나 내용을 입력한 뒤 임시저장해 주세요."
        return
    b = db.board(bid)
    if not b:
        S["write_draft_notice"] = "게시판을 찾지 못했어요. 다시 골라 주세요."
        return
    snapshot = {
        "id": uuid4().hex, "title": title, "body": body, "board": bid, "board_name": b["name"],
        "post_id": pid, "visibility": S.get("write_visibility", "public"),
        "extra": fields.to_store(b.get("fields"), fields.read(b.get("fields"), FP)),
        "saved_at": datetime.now(timezone(timedelta(hours=9))).strftime("%Y.%m.%d %H:%M:%S"),
    }
    S.setdefault("_write_saved_drafts", {}).setdefault(user["id"], []).insert(0, snapshot)
    S["_write_manual_draft_id"] = snapshot["id"]
    save_draft()
    S["write_draft_notice"] = "임시저장했어요."


# 2026.11.12 - yujin -"본인이 저장한 초안만 불러오고 게시판·원글 권한을 확인한 뒤 입력값을 복원해요."
def load_manual_draft(draft_id: str) -> None:
    draft = next((d for d in S.get("_write_saved_drafts", {}).get(user["id"], []) if d["id"] == draft_id), None)
    if not draft:
        S["write_draft_notice"] = "임시저장한 글을 찾지 못했어요."
        return
    b = db.board(draft["board"])
    pid = draft.get("post_id")
    if pid:
        original, _ = cd.get_post(pid)
        permitted = bool(original and (original["user"] == user["id"] or cd.is_admin(user)))
    else:
        permitted = any(item["id"] == draft["board"] for item in cd.writable_boards(user))
    if not b or not permitted:
        S["write_draft_notice"] = "지금은 이 게시판에서 글을 작성할 수 없어요. 임시저장한 글은 보관돼요."
        return
    reset(discard_draft=False)
    S["write_title"], S["write_body"] = draft["title"], draft["body"]
    S["write_board"] = S["_write_qp"] = b["id"]
    S["write_visibility"] = "public" if b["id"] == "obs" else draft.get("visibility", "public")
    for f in fields.active(b.get("fields")):
        value = draft.get("extra", {}).get(f["key"])
        if not fields.is_empty(value) and not fields.validate([{**f, "required": False}], {f["key"]: value}):
            S[f"{FP}_{f['key']}"] = fields._date(value) if f["type"] == "date" else value
    S["_write_draft_context"] = (user["id"], pid or "new")
    S["_write_manual_draft_id"] = draft_id
    if pid:
        S["_write_edit_loaded"] = pid
        st.query_params["edit"] = pid
    else:
        st.query_params.pop("edit", None)
    st.query_params["board"] = b["id"]
    S.pop("write_draft_dialog", None)
    S.pop("write_draft_notice", None)
    save_draft()


# 2026.11.12 - yujin -"본인의 임시저장 글을 삭제한 뒤 작성창도 초기화하고 다른 저장 글은 보존해요."
def delete_manual_draft(draft_id: str) -> None:
    saved = S.get("_write_saved_drafts", {}).get(user["id"], [])
    remaining = [item for item in saved if item["id"] != draft_id]
    if len(remaining) == len(saved):
        S["write_draft_notice"] = "임시저장한 글을 찾지 못했어요."
        return
    S["_write_saved_drafts"][user["id"]] = remaining
    if S.get("_write_manual_draft_id") == draft_id:
        S.pop("_write_manual_draft_id", None)
    for context, working in S.get("_write_drafts", {}).items():
        if context[0] == user["id"] and working.get("manual_draft_id") == draft_id:
            working.pop("manual_draft_id", None)
    # 2026.11.12 - yujin -"삭제 후 제목·본문·공개 설정·추가 항목·사진을 비우고 팝업은 갱신된 목록으로 유지해요."
    reset()
    st.query_params.pop("edit", None)
    S["_write_draft_context"] = (user["id"], "new")
    S["write_title"], S["write_body"] = "", ""
    S["write_visibility"] = "public"
    S["write_draft_dialog"] = True
    S["write_draft_notice"] = "임시저장한 글을 삭제했어요."


@st.dialog("임시저장한 글", on_dismiss=lambda: S.pop("write_draft_dialog", None))
def saved_drafts_dialog() -> None:
    # 2026.11.12 - yujin -"임시저장 개수를 누르면 사용자별 초안 목록과 불러오기 버튼을 팝업으로 보여줘요."
    drafts = S.get("_write_saved_drafts", {}).get(user["id"], [])
    st.caption("이 창의 로그인 세션에서 보관돼요. 사진은 다시 선택해 주세요.")
    st.caption(f"임시저장 {len(drafts)} / {MAX_SAVED_DRAFTS}개")
    if S.get("write_draft_notice"):
        st.info(S["write_draft_notice"])
    if not drafts:
        st.info("임시저장한 글이 없어요.")
    for item in drafts:
        with st.container(border=True):
            st.write(item["title"].strip() or "제목 없는 글")
            st.caption(f"{item['board_name']} · {item['saved_at']}")
            # 2026.11.12 - yujin -"기존 초안 카드에 삭제 버튼을 추가하고 팝업과 편집창의 저장 개수를 즉시 갱신해요."
            with st.container(horizontal=True, gap="small"):
                if st.button("불러오기", key=f"write_load_draft_{item['id']}",
                             on_click=load_manual_draft, args=(item["id"],)):
                    st.rerun()
                if st.button("삭제", key=f"write_delete_draft_{item['id']}", type="tertiary", icon=":material/delete:",
                             on_click=delete_manual_draft, args=(item["id"],)):
                    st.rerun()


# ---------- 시안 캡처용 상태 만들기 ----------
shot = st.query_params.get("shot")
if shot in ("dive", "errors", "cancel") and S.get("_write_shot") != shot:
    S["_write_shot"] = shot
    reset()
    S["write_board"] = S["_write_qp"] = "dive"
    if shot in ("dive", "cancel"):
        S["write_cancel"] = shot == "cancel"
        S["write_title"] = "섶섬 앞바다 스노클링 기록"
        S["write_body"] = ("물이 맑아서 바닥까지 잘 보였어요.\n"
                           "바위 틈에 보라성게가 모여 있어서 손대지 않고 눈으로만 봤어요.")
        S[f"{FP}_depth"] = 5
        S[f"{FP}_gear"] = "스노클링"
    else:
        S["write_title"] = "문섬 포인트에서 만난 군소"
        S["_write_shot_errors"] = True


# ---------- 저장 (올리기 버튼 콜백) ----------
def check(b: dict) -> dict:
    """화면에서 먼저 보는 검사 (사진 동의가 빠졌을 때만 써요. 서버와 같은 규칙)."""
    title = (S.get("write_title") or "").strip()
    body = (S.get("write_body") or "").strip()
    errs = {}
    if not title:
        errs["title"] = "제목이 비어 있어요. 무엇에 대한 글인지 적어 주세요."
    if not body:
        errs["body"] = "내용이 비어 있어요. 2,000자까지 쓸 수 있어요."
    errs.update(fields.validate(b.get("fields"), fields.read(b.get("fields"), FP)))
    if selected_photo() and not S.get("write_consent"):
        errs["photo"] = "사진을 올리려면 바로 위 동의에 체크해 주세요."
    return errs


def photo_id() -> str | None:
    """고른 사진을 서버에 맡겨요. 같은 사진이면 다시 올리지 않아요."""
    photo = selected_photo()
    if not photo:
        return None
    done = S.get("write_photo_id")
    if done and done[0] == photo.file_id:
        return done[1]
    pid = cd.upload_photo(photo)
    S["write_photo_id"] = (photo.file_id, pid)
    return pid


def submit(bid: str, pid: str | None) -> None:
    save_draft()
    b = db.board(bid)
    if selected_photo() and not S.get("write_consent"):  # 동의 없는 사진은 서버에 보내지 않아요
        S["write_errors"] = check(b)
        return
    title, body = S.get("write_title") or "", S.get("write_body") or ""
    extra = fields.to_store(b.get("fields"), fields.read(b.get("fields"), FP))
    # 2026.11.13 - yujin -"모임 모집의 고정 문구를 본문 끝에 한 번 붙여 실제 게시글에 저장하고 전체 2,000자 제한을 검사해요."
    notice = fixed_body_notice(b, extra)
    if notice:
        body = editable_body(b, body, extra)
        if not body.strip() or body.strip() == RECRUITMENT_TEMPLATE:
            S["write_errors"] = {"body": "내용이 비어 있어요. 모임에 대한 내용을 적어 주세요."}
            return
        body = body.rstrip() + "\n\n" + notice
        if len(body) > 2000:
            S["write_errors"] = {"body": "고정 문구를 포함해 2,000자까지 쓸 수 있어요. 내용을 조금 줄여 주세요."}
            return
    for attempt in range(2):
        try:
            ph = photo_id()
            if pid:
                cd.update_post(pid, title, body, extra, ph, bool(S.get("write_consent")),
                               visibility=S.get("write_visibility", "public"))
                S["community_toast"] = "고친 내용을 저장했어요"
            else:
                pid = cd.create_post(bid, title, body, extra, ph, bool(S.get("write_consent")),
                                     visibility=S.get("write_visibility", "public"))
                S["community_toast"] = "글을 올렸어요"
            break
        except cd.Problem as e:
            if e.code == "photo_expired":
                S.pop("write_photo_id", None)
                if attempt == 0 and selected_photo():
                    # 서버가 저장 전에 만료를 확인했을 때만, 고른 사진을 한 번 다시 올려요.
                    continue
            S["write_errors"] = cd.write_errors(e)
            return
        except ApiError as e:
            S["write_errors"] = {"_other": e.message}
            return
    # 2026.11.12 - yujin -"게시 성공 시 현재 사용한 초안만 제거하고 다른 임시저장 글은 유지해요."
    active_id = S.get("_write_manual_draft_id")
    context = S.get("_write_draft_context")
    if active_id and context:
        saved = S.get("_write_saved_drafts", {}).get(context[0], [])
        S["_write_saved_drafts"][context[0]] = [item for item in saved if item["id"] != active_id]
    reset()
    S["write_new_post"] = pid


def server_errors(bid: str) -> dict:
    """시안 캡처용: 지금 칸 값으로 서버 검사 결과를 받아요 (빈 칸이 있어 저장되지 않아요)."""
    b = db.board(bid)
    try:
        cd.create_post(bid, S.get("write_title") or "", S.get("write_body") or "",
                       fields.to_store(b.get("fields"), fields.read(b.get("fields"), FP)))
    except cd.Problem as e:
        return cd.write_errors(e)
    except ApiError as e:
        return {"_other": e.message}
    return {}


def ask_cancel(bid: str, pid: str | None) -> None:
    typed = any((S.get(k) or "") for k in ("write_title", "write_body")) or selected_photo()
    if typed:
        S["write_cancel"] = True
    else:  # 쓴 게 없으면 묻지 않고 돌아가요
        reset()
        S["write_cancel_go"] = {"post": pid} if pid else {"board": bid}


@st.dialog("글쓰기를 그만둘까요?", on_dismiss=lambda: S.pop("write_cancel", None))
def cancel_dialog(bid: str, pid: str | None) -> None:
    st.markdown("쓰던 내용이 사라져요. 그래도 그만둘까요?")
    st.caption("임시저장 버튼으로 저장한 글은 목록에 남아요.")
    with st.container(horizontal=True, gap="small"):
        if st.button("그만두고 목록으로", type="primary", key="write_cancel_ok"):
            reset()
            if pid:
                nav.go("community", post=pid)
            nav.go("community", board=bid)
        if st.button("계속 쓰기", key="write_cancel_back"):
            S.pop("write_cancel", None)
            st.rerun()


def tips(b: dict) -> None:
    # 2026.11.12 - yujin -"작성 안내를 placeholder로 옮긴 자리에 일반 게시글의 공개 설정을 배치해요."
    with st.container(border=True):
        st.markdown("**공개 설정**")
        if b["id"] == "obs":
            st.caption("관찰 이야기는 기존 공유 설정과 게시판 읽기 권한을 따라요.")
        else:
            labels = {"public": "전체 공개", "members": "멤버 공개"}
            st.radio("공개 범위", list(labels), format_func=labels.get, key="write_visibility", on_change=save_draft)
            st.caption("멤버 공개 글은 로그인한 회원만 볼 수 있어요. 게시판의 읽기 권한도 함께 적용돼요.")
    with st.container(border=True):
        st.markdown("**관찰 이야기를 올리고 싶나요?**")
        ui.muted("생물 사진은 ‘생물 알아보기’에서 이름을 알아본 뒤 수족관에 담으면서 공유할 수 있어요.")
        nav.link("identify", "생물 알아보기", icon=":material/photo_camera:")


# ---------- 화면 그리기 ----------
if not user:
    ui.page_header("글쓰기", "궁금해요 · 자유 수다 같은 게시판에 글을 남겨요.")
    ui.empty_state("🐚", "로그인하면 글을 쓸 수 있어요. 로그인 뒤 이 화면으로 다시 돌아와요.")
    if st.button("로그인 / 회원가입", type="primary", icon=":material/login:", key="write_login"):
        nav.remember_here("write", **({"board": qp_board} if qp_board else {}))
        S["after_login"] = S.get("back_to")
        nav.go("login")
    st.stop()

post = cd.get_post(edit_id)[0] if edit_id else None
# 2026.11.12 - yujin -"다른 화면에서 돌아오면 작성창을 초기화하고 초안은 목록에서 명시적으로 불러올 때만 복원해요."
draft_context = (user["id"], edit_id or "new")
needs_load = S.get("_write_draft_context") != draft_context or any(k not in S for k in ("write_title", "write_body"))
if needs_load:
    S.get("_write_drafts", {}).pop(draft_context, None)
    S.pop("write_draft_notice", None)
    S.pop("write_draft_dialog", None)
if edit_id:
    if not post or not (post["user"] == user["id"] or cd.is_admin(user)):
        ui.page_header("글 고치기")
        ui.empty_state("🐚", "고칠 수 없는 글이에요. 내가 쓴 글만 고칠 수 있어요.")
        nav.link("community", "커뮤니티로 가기", icon=":material/arrow_back:")
        st.stop()
    board = db.board(post["board"])
    if needs_load or S.get("_write_edit_loaded") != edit_id:
        reset(discard_draft=False)
        S["write_title"] = post["title"]
        S["write_body"] = editable_body(board, post.get("body", ""), post.get("extra") or {})
        # 2026.11.12 - yujin -"수정 화면 재진입 시에는 게시된 원문과 공개 설정을 표시해요."
        S["write_visibility"] = "public" if post["board"] == "obs" else post.get("visibility", "public")
        for f in fields.active(board.get("fields")):
            v = (post.get("extra") or {}).get(f["key"])
            if fields.is_empty(v) or fields.validate([{**f, "required": False}], {f["key"]: v}):
                continue
            S[f"{FP}_{f['key']}"] = fields._date(v) if f["type"] == "date" else v
        # 2026.11.12 - yujin -"원문 입력값을 채운 뒤 로딩 완료를 기록해 편집 중 재실행에서는 유지해요."
        S["_write_edit_loaded"] = edit_id
    ui.page_header("글 고치기", "고친 내용은 저장하면 바로 보여요.")
else:
    if needs_load:
        reset(discard_draft=False)
        S["write_title"], S["write_body"] = "", ""
        S["write_visibility"] = "public"
    options = cd.writable_boards(user)
    ids = [b["id"] for b in options]
    ui.page_header("글쓰기", "궁금한 것, 나누고 싶은 바다 이야기를 남겨요.")
    if qp_board and (needs_load or qp_board != S.get("_write_qp")):
        S["_write_qp"] = qp_board
        if qp_board in ids:
            S["write_board"] = qp_board
    if qp_board == "obs":
        st.info("관찰 이야기는 ‘생물 알아보기’에서 수족관에 담으면서 공유해요. 다른 게시판을 골라 주세요.",
                icon=":material/info:")
    if not ids:
        ui.empty_state("🐚", "지금 글을 쓸 수 있는 게시판이 없어요.", "커뮤니티로 가기", "community", key="write_none")
        st.stop()
    if S.get("write_board") not in ids:
        S["write_board"] = ids[0]
    board = None

S["_write_draft_context"] = draft_context
errs = S.get("write_errors") or {}
if S.pop("_write_shot_errors", False):
    errs = S["write_errors"] = server_errors("dive")

# 2026.11.13 - yujin -"모바일에서 편집창 다음 공개 설정이 이어지도록 두 열에 전용 반응형 범위를 지정해요."
with st.container(key="write_layout"):
    left, right = st.columns([3, 2], gap="large")
with left:
    if post:
        ui.chips(ui.chip(f"게시판 · {board['name']}"))
    else:
        st.selectbox("게시판", ids, format_func=lambda i: db.board(i)["name"], key="write_board",
                     on_change=lambda: S.pop("write_errors", None))
        board = db.board(S["write_board"])
    st.caption(board.get("desc", ""))

    # 2026.11.12 - yujin -"폼 밖에서 입력 변경을 즉시 받아 페이지를 떠나기 전 제목·본문을 임시 저장해요."
    # 2026.11.12 - yujin -"제목·내용 placeholder만 연하게 표시해요."
    st.html("""<style>
        .st-key-write_editor input::placeholder,
        .st-key-write_editor textarea::placeholder {
            color:rgba(18,52,77,0.42) !important;
            opacity:1 !important;
        }
    </style>""")
    with st.container(border=True, key="write_editor"):
        # 2026.11.13 - yujin -"바다지킴이의 작성 유형을 제목 위에 한 번만 표시하며 기존 항목 키·저장·초안 복원을 유지해요."
        promoted = [f for f in fields.active(board.get("fields")) if board["id"] == "environment"
                    and f["key"] == "writetype" and f.get("type") == "single"]
        if promoted:
            fields.render(promoted, FP, errors=errs)
        title_hint, body_hint = editor_placeholders(board)
        # 2026.11.12 - yujin -"별도 안내 카드 대신 제목·내용 placeholder에 작성 안내를 표시해요."
        # 2026.11.13 - yujin -"작성 유형을 변경해도 제목·본문 위젯 키를 유지해 입력한 내용이 사라지지 않게 해요."
        st.text_input("제목 *", max_chars=50, key="write_title", placeholder=title_hint, on_change=save_draft)
        fields.error_text(errs.get("title"))
        # 2026.11.13 - yujin -"입력 영역의 같은 테두리 안에 수정 불가 고정 문구를 배치하고 입력한 본문은 그대로 유지해요."
        notice = fixed_body_notice(board, fields.read(board.get("fields"), FP))
        # 2026.11.13 - yujin -"빈 새 글에서 모집 유형을 선택할 때만 양식을 채우며 기존 입력·수정 글·불러온 초안은 덮어쓰지 않아요."
        body_mode = (board["id"], bool(notice))
        # 2026.11.13 - yujin -"모집 유형을 떠날 때 손대지 않은 자동 양식만 비워 실천 공유 placeholder가 표시되게 해요."
        if not notice and S.get("_write_auto_recruitment_template") and S.get("_write_body_mode") != body_mode:
            if S.get("write_body") == RECRUITMENT_TEMPLATE:
                S["write_body"] = ""
                save_draft()
            S.pop("_write_auto_recruitment_template", None)
        if (notice and S.get("_write_body_mode") != body_mode and not post
                and not S.get("_write_manual_draft_id") and not (S.get("write_body") or "").strip()):
            S["write_body"] = RECRUITMENT_TEMPLATE
            S["_write_auto_recruitment_template"] = True
            save_draft()
        S["_write_body_mode"] = body_mode
        if notice:
            st.markdown("내용 *")
            # 2026.11.13 - yujin -"모집 본문과 고정 문구의 배경·테두리를 통일하고 내용에 맞춰 높이가 늘어나게 해 빈 여백을 줄여요."
            st.html("""<style>
                /* 2026.11.13 - yujin -"모집 본문 전체 높이를 늘리되 고정 문구는 placeholder 바로 아래에 유지해요." */
                .st-key-write_body_group {border:1px solid #DCE6E9; border-radius:10px; min-height:260px;
                    padding:0; overflow:hidden; gap:0 !important; background:#EAF6F7;}
                .st-key-write_body_group [data-baseweb="textarea"],
                .st-key-write_body_group [data-testid="stTextAreaRootElement"],
                .st-key-write_body_group textarea {border:0 !important; border-radius:0 !important;
                    box-shadow:none !important; background:#EAF6F7 !important;}
                .st-key-write_body_group textarea {padding:12px 12px 0 !important; resize:none;}
                .st-key-write_body_group [data-testid="stMarkdownContainer"] {padding:0 12px 12px;}
                .st-key-write_body_group p {margin:0; font-size:inherit; color:#12344D;}
            </style>""")
        with st.container(key="write_body_group"):
            # 2026.11.13 - yujin -"일반 본문 입력칸의 기본 높이를 180px에서 260px로 늘려 작성 공간을 확보해요."
            st.text_area("내용 *", max_chars=2000, height="content" if notice else 260, key="write_body",
                         placeholder=body_hint, on_change=save_draft,
                         label_visibility="collapsed" if notice else "visible")
            if notice:
                st.markdown(notice)
        fields.error_text(errs.get("body"))
        st.caption("임시저장을 누르면 작성 중인 글을 목록에 보관할 수 있어요. 사진은 다시 선택해 주세요.")
        # 2026.11.13 - yujin -"위로 옮긴 작성 유형을 추가 항목에서 중복 표시하지 않아요."
        remaining_fields = [f for f in fields.active(board.get("fields")) if f not in promoted]
        if remaining_fields:
            st.markdown(f"**{board['name']} 추가 항목**")
            fields.render(remaining_fields, FP, errors=errs)
        if cd.can(user, board["perm"]["photo"]):
            st.file_uploader("사진 1장 (선택)", type=db.settings()["formats"], key=photo_key(),
                             max_upload_size=int(db.settings()["max_mb"]),
                             help=f"JPG · PNG, {db.settings()['max_mb']}MB까지. 스마트폰에서는 사진 찍기나 앨범을 고를 수 있어요.")
            st.checkbox("사진을 사이트에서 보여줘도 괜찮아요 (사진을 올릴 때 필수)", key="write_consent",
                        help="올린 사진은 글을 지우면 함께 지워져요.")
            fields.error_text(errs.get("photo"))
        n_err = len([k for k in errs if not k.startswith("_")])
        if n_err:
            st.error(f"고쳐야 할 칸이 {n_err}개 있어요. 칸 아래 안내를 확인해 주세요. 입력한 내용은 그대로 있어요.",
                     icon=":material/error:")
        if errs.get("_wait"):
            st.warning(errs["_wait"], icon=":material/schedule:")
        if errs.get("_other"):
            st.error(errs["_other"], icon=":material/error:")
        with st.container(horizontal=True, gap="small", key="write_actions"):
            st.button("고친 내용 저장하기" if post else "올리기", type="primary", icon=":material/send:",
                                  on_click=submit, args=(board["id"], edit_id))
            # 2026.11.12 - yujin -"임시저장과 개수 버튼을 하나의 둥근 테두리와 가운데 구분선으로 연결해요."
            st.html("""<style>
                .st-key-write_draft_actions {gap:0 !important; border:1px solid #DCE6E9;
                    border-radius:10px; overflow:hidden; background:white;}
                .st-key-write_draft_actions button {border:0 !important; border-radius:0 !important;
                    background:transparent; box-shadow:none !important; min-height:44px;}
                .st-key-write_draft_actions button:hover {background:#EAF6F7;}
                .st-key-write_draft_count {position:relative;}
                .st-key-write_draft_count::before {content:''; position:absolute; left:0;
                    top:25%; height:50%; width:1px; background:#DCE6E9; pointer-events:none;}
                .st-key-write_draft_count button {min-width:42px; font-weight:600;}
            </style>""")
            with st.container(horizontal=True, width="content", gap=None, key="write_draft_actions"):
                st.button("임시저장", key="write_save_draft", on_click=save_manual_draft, args=(board["id"], edit_id))
                draft_count = len(S.get("_write_saved_drafts", {}).get(user["id"], []))
                st.button(str(draft_count), key="write_draft_count", help="임시저장한 글 목록",
                          on_click=lambda: S.update(write_draft_dialog=True, write_cancel=False))
            st.button("취소", on_click=ask_cancel, args=(board["id"], edit_id))
        if S.get("write_draft_notice"):
            st.caption(S["write_draft_notice"])
with right:
    tips(board)

if S.get("write_cancel"):
    cancel_dialog(board["id"], edit_id)
elif S.get("write_draft_dialog"):
    saved_drafts_dialog()
