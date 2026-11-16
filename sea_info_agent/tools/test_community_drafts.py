"""차유진 담당: 화면 이동 후 글쓰기 초안 복원 검사. 실제 게시글은 변경하지 않아요."""
# 2026.11.12 - yujin -"초안의 화면 이동 복원·사용자/글별 분리·게시/취소 후 초기화를 검사해요."
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.user = {"id": "author", "role": "user"}
        self.board = {"id": "public", "name": "자유 수다", "desc": "", "type": "일반형",
                      "fields": [], "perm": {"photo": "관리자"}}
        self.posts = {pid: {"id": pid, "user": "author", "board": "public", "title": f"원래 제목 {pid}",
                            "body": f"원래 본문 {pid}", "extra": {}} for pid in ("p1", "p2")}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)

        def go(*args, **kwargs):
            st.session_state["_test_away"] = True
            st.rerun()

        targets = {
            "services.auth.current_user": lambda: dict(self.user),
            "services.db.board": lambda bid: deepcopy(self.board),
            "services.db.boards": lambda **kwargs: [deepcopy(self.board)],
            "services.community_data.writable_boards": lambda user: [deepcopy(self.board)],
            "services.community_data.get_post": lambda pid: (deepcopy(self.posts[pid]), None),
            "components.nav.link": lambda *args, **kwargs: None,
            "components.nav.go": go,
        }
        for target, callback in targets.items():
            self.stack.enter_context(patch(target, side_effect=callback))
        self.create = self.stack.enter_context(patch("services.community_data.create_post", return_value="created"))
        self.at = AppTest.from_string(f"""
import streamlit as st
from runpy import run_path
if st.session_state.get('_test_away'):
    st.button('글쓰기로 돌아가기', key='test_return', on_click=lambda: st.session_state.update(_test_away=False))
else:
    st.button('다른 화면으로', key='test_leave', on_click=lambda: st.session_state.update(_test_away=True))
    run_path({str(ROOT / 'views/write.py')!r})
""")

    def enter(self, edit=None):
        self.at.query_params.clear()
        if edit:
            self.at.query_params["edit"] = edit
        self.at.run()
        self.assertFalse(self.at.exception)

    def type_draft(self, title, body):
        self.at.text_input(key="write_title").set_value(title).run()
        self.at.text_area(key="write_body").set_value(body).run()
        self.assertFalse(self.at.exception)

    def leave(self):
        self.at.button(key="test_leave").click().run()
        self.at.run()  # 입력 위젯이 없는 화면에서 위젯 상태 정리까지 진행해요.
        self.assertFalse(self.at.exception)

    def return_to_write(self):
        self.at.button(key="test_return").click().run()
        self.assertFalse(self.at.exception)

    # 2026.11.12 - yujin -"글쓰기 재진입 시 작성창은 비우고 버튼으로 저장한 글만 목록에서 복원해요."
    def test_new_editor_clears_on_navigation_and_saved_draft_loads_explicitly(self):
        self.enter()
        # 2026.11.12 - yujin -"placeholder 안내와 멤버 공개 선택의 초안 복원·게시 전달을 확인해요."
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "무엇에 대한 글인가요?")
        self.assertIn("사람 얼굴이나 차 번호판", self.at.text_area(key="write_body").proto.placeholder)
        self.assertFalse(any("이렇게 쓰면 좋아요" in item.value for item in self.at.markdown))
        self.at.radio(key="write_visibility").set_value("members").run()
        self.type_draft("작성 중 제목", "작성 중 본문")
        self.at.button(key="write_save_draft").click().run()
        draft_id = self.at.session_state["_write_manual_draft_id"]
        self.leave()
        self.return_to_write()
        self.assertEqual(self.at.text_input(key="write_title").value, "")
        self.assertEqual(self.at.text_area(key="write_body").value, "")
        self.assertEqual(self.at.radio(key="write_visibility").value, "public")
        self.assertEqual(self.at.button(key="write_draft_count").label, "1")
        self.at.button(key="write_draft_count").click().run()
        self.at.button(key=f"write_load_draft_{draft_id}").click().run()
        self.assertEqual(self.at.text_input(key="write_title").value, "작성 중 제목")
        self.assertEqual(self.at.text_area(key="write_body").value, "작성 중 본문")
        self.assertEqual(self.at.radio(key="write_visibility").value, "members")
        next(b for b in self.at.button if b.label == "올리기").click().run()
        self.assertFalse(self.at.exception)
        self.create.assert_called_once()
        self.assertEqual(self.create.call_args.kwargs["visibility"], "members")
        self.return_to_write()
        self.assertEqual(self.at.text_input(key="write_title").value, "")
        self.assertEqual(self.at.text_area(key="write_body").value, "")
        self.assertEqual(self.at.radio(key="write_visibility").value, "public")

    def test_edit_draft_is_separate_from_other_posts_and_new_post(self):
        self.posts["p1"]["visibility"] = "members"
        self.enter("p1")
        self.assertEqual(self.at.radio(key="write_visibility").value, "members")
        self.type_draft("p1 수정 초안", "p1 수정 내용")
        self.leave()
        self.at.query_params["edit"] = "p2"
        self.return_to_write()
        self.assertEqual(self.at.text_input(key="write_title").value, "원래 제목 p2")
        self.leave()
        self.at.query_params.clear()
        self.return_to_write()
        self.assertEqual(self.at.text_input(key="write_title").value, "")
        self.leave()
        self.at.query_params["edit"] = "p1"
        self.return_to_write()
        self.assertEqual(self.at.text_input(key="write_title").value, "원래 제목 p1")
        self.assertEqual(self.at.text_area(key="write_body").value, "원래 본문 p1")

    def test_user_drafts_do_not_mix_and_explicit_cancel_discards(self):
        self.enter()
        self.type_draft("내 초안", "내 내용")
        self.leave()
        self.user["id"] = "other"
        self.return_to_write()
        self.assertEqual(self.at.text_input(key="write_title").value, "")
        self.leave()
        self.user["id"] = "author"
        self.return_to_write()
        self.assertEqual(self.at.text_input(key="write_title").value, "")
        self.type_draft("새로 작성한 제목", "새로 작성한 내용")
        next(b for b in self.at.button if b.label == "취소").click().run()
        self.at.button(key="write_cancel_ok").click().run()
        self.assertFalse(self.at.exception)
        self.return_to_write()
        self.assertEqual(self.at.text_input(key="write_title").value, "")

    # 2026.11.12 - yujin -"임시저장 버튼의 개수 증가·화면 유지·팝업 선택 복원·게시 후 선택 초안 제거를 검사해요."
    def test_manual_snapshots_count_popup_restore_and_publish(self):
        self.enter()
        self.assertEqual(self.at.button(key="write_draft_count").label, "0")
        self.at.radio(key="write_visibility").set_value("members").run()
        self.type_draft("첫 초안", "첫 초안 내용")
        self.at.button(key="write_save_draft").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.button(key="write_draft_count").label, "1")
        self.assertEqual(self.at.text_input(key="write_title").value, "첫 초안")
        self.assertNotIn("_test_away", self.at.session_state)
        first_id = self.at.session_state["_write_saved_drafts"]["author"][0]["id"]
        self.type_draft("두 번째 초안", "두 번째 초안 내용")
        self.at.button(key="write_save_draft").click().run()
        self.assertEqual(self.at.button(key="write_draft_count").label, "2")
        self.leave()
        self.return_to_write()
        self.assertEqual(self.at.button(key="write_draft_count").label, "2")
        self.at.button(key="write_draft_count").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(len([b for b in self.at.button if str(b.key).startswith("write_load_draft_")]), 2)
        self.at.button(key=f"write_load_draft_{first_id}").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.text_input(key="write_title").value, "첫 초안")
        self.assertEqual(self.at.text_area(key="write_body").value, "첫 초안 내용")
        self.assertEqual(self.at.radio(key="write_visibility").value, "members")
        self.assertEqual(self.at.button(key="write_draft_count").label, "2")
        next(b for b in self.at.button if b.label == "올리기").click().run()
        self.assertFalse(self.at.exception)
        self.return_to_write()
        self.assertEqual(self.at.button(key="write_draft_count").label, "1")
        self.assertNotIn(first_id, [d["id"] for d in self.at.session_state["_write_saved_drafts"]["author"]])

    def test_manual_draft_restores_extra_fields_and_edit_context(self):
        self.board["fields"] = [{"key": "depth", "label": "수심", "type": "number", "min": 0, "max": 40}]
        self.enter("p1")
        self.type_draft("수정 중 초안", "수정 중 내용")
        self.at.number_input[0].set_value(5.5).run()
        self.at.button(key="write_save_draft").click().run()
        draft_id = self.at.session_state["_write_saved_drafts"]["author"][0]["id"]
        self.leave()
        self.at.query_params.clear()
        self.return_to_write()
        self.at.button(key="write_draft_count").click().run()
        self.at.button(key=f"write_load_draft_{draft_id}").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.query_params["edit"], "p1")
        self.assertEqual(self.at.text_input(key="write_title").value, "수정 중 초안")
        self.assertEqual(self.at.number_input[0].value, 5.5)

    def test_empty_manual_draft_is_not_added_and_saved_drafts_are_user_scoped(self):
        self.enter()
        self.at.button(key="write_save_draft").click().run()
        self.assertEqual(self.at.button(key="write_draft_count").label, "0")
        self.type_draft("내가 저장한 글", "내용")
        self.at.button(key="write_save_draft").click().run()
        self.leave()
        self.user["id"] = "other"
        self.return_to_write()
        self.assertEqual(self.at.button(key="write_draft_count").label, "0")
        self.leave()
        self.user["id"] = "author"
        self.return_to_write()
        self.type_draft("취소할 작성 내용", "내용")
        next(b for b in self.at.button if b.label == "취소").click().run()
        self.at.button(key="write_cancel_ok").click().run()
        self.return_to_write()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.text_input(key="write_title").value, "")
        self.assertEqual(self.at.button(key="write_draft_count").label, "1")


    # 2026.11.12 - yujin -"6번째 저장을 거절해도 기존 5개 초안과 현재 입력값이 유지되는지 확인해요."
    def test_manual_draft_limit_preserves_existing_drafts_and_editor(self):
        self.enter()
        for i in range(5):
            self.type_draft(f"초안 {i + 1}", f"본문 {i + 1}")
            self.at.button(key="write_save_draft").click().run()
            self.assertFalse(self.at.exception)
            self.assertEqual(self.at.button(key="write_draft_count").label, str(i + 1))
        saved_ids = [d["id"] for d in self.at.session_state["_write_saved_drafts"]["author"]]
        self.type_draft("여섯 번째 입력", "보존할 내용")
        self.at.button(key="write_save_draft").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.button(key="write_draft_count").label, "5")
        self.assertEqual(saved_ids, [d["id"] for d in self.at.session_state["_write_saved_drafts"]["author"]])
        self.assertEqual(self.at.text_input(key="write_title").value, "여섯 번째 입력")
        self.assertEqual(self.at.text_area(key="write_body").value, "보존할 내용")
        self.assertIn("최대 5개", self.at.session_state["write_draft_notice"])
        self.leave()
        self.user["id"] = "other"
        self.return_to_write()
        self.type_draft("다른 회원 초안", "다른 회원 내용")
        self.at.button(key="write_save_draft").click().run()
        self.assertEqual(self.at.button(key="write_draft_count").label, "1")


    # 2026.11.12 - yujin -"목록 삭제 시 개수 감소·5개 한도 해제·작성창 초기화·마지막 글 삭제를 확인해요."
    def test_delete_manual_draft_updates_count_and_resets_editor(self):
        self.enter()
        for i in range(5):
            self.type_draft(f"저장 글 {i}", f"저장 내용 {i}")
            self.at.button(key="write_save_draft").click().run()
        active_id = self.at.session_state["_write_manual_draft_id"]
        self.type_draft("현재 작성 중인 제목", "현재 작성 중인 내용")
        self.at.button(key="write_draft_count").click().run()
        self.at.button(key=f"write_delete_draft_{active_id}").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.button(key="write_draft_count").label, "4")
        self.assertEqual(len([b for b in self.at.button if str(b.key).startswith("write_load_draft_")]), 4)
        self.assertEqual(self.at.text_input(key="write_title").value, "")
        self.assertEqual(self.at.text_area(key="write_body").value, "")
        self.assertEqual(self.at.radio(key="write_visibility").value, "public")
        self.assertNotIn("_write_manual_draft_id", self.at.session_state)
        self.assertTrue(all(d.get("manual_draft_id") != active_id for d in self.at.session_state["_write_drafts"].values()))
        self.at.session_state["write_draft_dialog"] = False
        self.at.run()
        self.type_draft("다시 작성한 제목", "다시 작성한 내용")
        self.at.button(key="write_save_draft").click().run()
        self.assertEqual(self.at.button(key="write_draft_count").label, "5")
        self.at.button(key="write_draft_count").click().run()
        for record in list(self.at.session_state["_write_saved_drafts"]["author"]):
            self.at.button(key=f"write_delete_draft_{record['id']}").click().run()
            self.assertFalse(self.at.exception)
        self.assertEqual(self.at.button(key="write_draft_count").label, "0")
        self.assertFalse([b for b in self.at.button if str(b.key).startswith("write_load_draft_")])
        self.assertTrue(any("임시저장한 글이 없어요" in item.value for item in self.at.info))

    # 2026.11.12 - yujin -"수정 글의 초안 삭제 후에도 원문이 다시 채워지지 않고 새 작성창이 비어 있는지 확인해요."
    def test_delete_edit_draft_opens_empty_new_editor(self):
        self.enter("p1")
        self.type_draft("수정하던 제목", "수정하던 내용")
        self.at.button(key="write_save_draft").click().run()
        draft_id = self.at.session_state["_write_manual_draft_id"]
        self.at.button(key="write_draft_count").click().run()
        self.at.button(key=f"write_delete_draft_{draft_id}").click().run()
        self.assertFalse(self.at.exception)
        self.assertNotIn("edit", self.at.query_params)
        self.assertEqual(self.at.text_input(key="write_title").value, "")
        self.assertEqual(self.at.text_area(key="write_body").value, "")
        self.assertEqual(self.at.button(key="write_draft_count").label, "0")

    # 2026.11.13 - yujin -"작성 유형별 안내 변경, 입력 유지, 다른 게시판 안내 유지와 초안·수정 글 복원을 검사해요."
    def environment_board(self):
        self.board.update(id="environment", name="바다지킴이", fields=[
            {"key": "writetype", "label": "작성 유형", "type": "single", "required": True,
             "options": ["실천공유", "모임모집"]}])

    def test_environment_placeholders_change_without_losing_text_or_duplicate_selector(self):
        self.environment_board()
        self.enter()
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "무엇에 대한 글인가요?")
        self.at.radio(key="write_f_writetype").set_value("실천공유").run()
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "환경을 위해 어떤 실천을 했나요?")
        self.assertEqual(self.at.text_area(key="write_body").proto.placeholder,
                         "어디서, 언제, 어떤 활동을 했는지 자유롭게 알려주세요.\n활동하며 느낀 점이나 함께 나누고 싶은 팁도 좋아요.")
        self.type_draft("보존할 제목", "보존할 본문")
        self.at.radio(key="write_f_writetype").set_value("모임모집").run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "함께할 환경 활동을 소개해주세요.")
        self.assertEqual(self.at.text_area(key="write_body").proto.placeholder,
                         "참여할 분들이 찾아올 수 있도록 아래 내용을 적어주세요.")
        self.assertEqual(self.at.text_input(key="write_title").value, "보존할 제목")
        self.assertEqual(self.at.text_area(key="write_body").value, "보존할 본문")
        self.assertEqual(len([r for r in self.at.radio if r.key == "write_f_writetype"]), 1)
        next(b for b in self.at.button if b.label == "올리기").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.create.call_args.args[3], {"writetype": "모임모집"})

    def test_environment_draft_and_edit_restore_selected_placeholder(self):
        self.environment_board()
        self.enter()
        self.at.radio(key="write_f_writetype").set_value("모임모집").run()
        self.type_draft("모임 초안", "모임 내용")
        self.at.button(key="write_save_draft").click().run()
        draft_id = self.at.session_state["_write_manual_draft_id"]
        self.leave()
        self.return_to_write()
        self.at.button(key="write_draft_count").click().run()
        self.at.button(key=f"write_load_draft_{draft_id}").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.radio(key="write_f_writetype").value, "모임모집")
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "함께할 환경 활동을 소개해주세요.")
        self.assertEqual(self.at.text_input(key="write_title").value, "모임 초안")
        self.leave()
        self.posts["p1"].update(board="environment", extra={"writetype": "실천공유"})
        self.at.query_params["edit"] = "p1"
        self.return_to_write()
        self.assertEqual(self.at.radio(key="write_f_writetype").value, "실천공유")
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "환경을 위해 어떤 실천을 했나요?")
        self.assertEqual(self.at.text_input(key="write_title").value, "원래 제목 p1")

    # 2026.11.13 - yujin -"실제 게시판의 띄어쓰기 있는 선택지로 안내 변경을 재현하고 원래 선택지로 저장되는지 검사해요."
    def test_environment_actual_spaced_options_change_placeholders_and_preserve_stored_value(self):
        self.environment_board()
        self.board["fields"][0].update(label="글 유형", options=["실천 공유", "모임 모집"])
        self.enter()
        self.at.radio(key="write_f_writetype").set_value("실천 공유").run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "환경을 위해 어떤 실천을 했나요?")
        self.assertIn("활동하며 느낀 점", self.at.text_area(key="write_body").proto.placeholder)
        self.type_draft("실천 제목", "실천 본문")
        self.at.radio(key="write_f_writetype").set_value("모임 모집").run()
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "함께할 환경 활동을 소개해주세요.")
        self.assertEqual(self.at.text_area(key="write_body").proto.placeholder,
                         "참여할 분들이 찾아올 수 있도록 아래 내용을 적어주세요.")
        self.assertEqual(self.at.text_input(key="write_title").value, "실천 제목")
        self.assertEqual(self.at.text_area(key="write_body").value, "실천 본문")
        # 2026.11.13 - yujin -"모집 고정 문구가 입력 영역에 표시되고 실제 게시글 본문에 포함되는지 검사해요."
        notice = "함께하실 분은 댓글로 참여 의사를 남겨주세요!"
        self.assertTrue(any(item.value == notice for item in self.at.markdown))
        self.at.radio(key="write_f_writetype").set_value("실천 공유").run()
        self.assertFalse(any(item.value == notice for item in self.at.markdown))
        self.at.radio(key="write_f_writetype").set_value("모임 모집").run()
        self.assertTrue(any(item.value == notice for item in self.at.markdown))
        next(b for b in self.at.button if b.label == "올리기").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.create.call_args.args[3], {"writetype": "모임 모집"})
        self.assertEqual(self.create.call_args.args[2], "실천 본문\n\n" + notice)

    # 2026.11.13 - yujin -"기존 모집 글 재수정 시 고정 문구 중복을 막고 실천 공유로 바꾸면 저장하지 않아요."
    def test_recruitment_edit_separates_fixed_body_and_saves_once(self):
        self.environment_board()
        notice = "함께하실 분은 댓글로 참여 의사를 남겨주세요!"
        self.posts["p1"].update(board="environment", body="기존 모임 내용\n\n" + notice,
                                extra={"writetype": "모임모집"})
        with patch("services.community_data.update_post", return_value={}) as update:
            self.enter("p1")
            self.assertEqual(self.at.text_area(key="write_body").value, "기존 모임 내용")
            self.assertTrue(any(item.value == notice for item in self.at.markdown))
            next(b for b in self.at.button if b.label == "고친 내용 저장하기").click().run()
            self.assertFalse(self.at.exception)
            self.assertEqual(update.call_args.args[2], "기존 모임 내용\n\n" + notice)
            self.return_to_write()
            self.at.radio(key="write_f_writetype").set_value("실천공유").run()
            next(b for b in self.at.button if b.label == "고친 내용 저장하기").click().run()
            self.assertEqual(update.call_args.args[2], "기존 모임 내용")

    # 2026.11.13 - yujin -"고정 문구만으로 게시되지 않으며 합친 본문 길이 초과 시 입력값을 보존해요."
    def test_recruitment_empty_and_combined_body_limit(self):
        self.environment_board()
        self.enter()
        self.at.radio(key="write_f_writetype").set_value("모임모집").run()
        self.at.text_input(key="write_title").set_value("모집 제목").run()
        next(b for b in self.at.button if b.label == "올리기").click().run()
        self.create.assert_not_called()
        self.assertIn("내용이 비어", self.at.session_state["write_errors"]["body"])
        self.at.text_area(key="write_body").set_value("가" * 2000).run()
        next(b for b in self.at.button if b.label == "올리기").click().run()
        self.create.assert_not_called()
        self.assertIn("고정 문구를 포함", self.at.session_state["write_errors"]["body"])
        self.assertEqual(self.at.text_area(key="write_body").value, "가" * 2000)

    # 2026.11.13 - yujin -"모집 양식 직접 입력·유형 전환 보존·초안 복원·참여 안내 결합 저장을 검사해요."
    def test_recruitment_template_is_editable_preserved_and_saved_with_fixed_notice(self):
        self.environment_board()
        self.enter()
        template = "● 활동 내용 :\n● 모임 날짜와 시간 :\n● 집결 장소:\n● 예상 소요 시간 :\n● 준비물 :"
        self.at.radio(key="write_f_writetype").set_value("모임모집").run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.text_area(key="write_body").value, template)
        completed = template.replace("● 활동 내용 :", "● 활동 내용 : 해변 쓰레기 줍기")
        self.type_draft("함께 청소해요", completed)
        self.at.radio(key="write_f_writetype").set_value("실천공유").run()
        self.at.radio(key="write_f_writetype").set_value("모임모집").run()
        self.assertEqual(self.at.text_area(key="write_body").value, completed)
        self.at.button(key="write_save_draft").click().run()
        draft_id = self.at.session_state["_write_manual_draft_id"]
        self.leave()
        self.return_to_write()
        self.at.button(key="write_draft_count").click().run()
        self.at.button(key=f"write_load_draft_{draft_id}").click().run()
        self.assertEqual(self.at.text_area(key="write_body").value, completed)
        next(b for b in self.at.button if b.label == "올리기").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.create.call_args.args[2], completed + "\n\n함께하실 분은 댓글로 참여 의사를 남겨주세요!")

    # 2026.11.13 - yujin -"사용자가 양식을 비운 뒤에는 일반 재실행으로 다시 채우지 않아요."
    def test_deleted_recruitment_template_does_not_return_on_rerun(self):
        self.environment_board()
        self.enter()
        self.at.radio(key="write_f_writetype").set_value("모임모집").run()
        self.at.text_area(key="write_body").set_value("").run()
        self.at.run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.text_area(key="write_body").value, "")

    # 2026.11.13 - yujin -"모집에서 실천 공유로 바꾸면 미작성 자동 양식만 지워 안내가 보이고 직접 입력한 내용은 보존되는지 검사해요."
    def test_switch_to_practice_clears_only_untouched_automatic_template(self):
        self.environment_board()
        self.board["fields"][0]["options"] = ["실천 공유", "모임 모집"]
        self.enter()
        self.at.radio(key="write_f_writetype").set_value("모임 모집").run()
        self.assertIn("● 활동 내용 :", self.at.text_area(key="write_body").value)
        self.at.text_input(key="write_title").set_value("작성 중 제목").run()
        self.at.radio(key="write_f_writetype").set_value("실천 공유").run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.text_area(key="write_body").value, "")
        self.assertEqual(self.at.text_input(key="write_title").value, "작성 중 제목")
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "환경을 위해 어떤 실천을 했나요?")
        self.assertIn("어디서, 언제, 어떤 활동", self.at.text_area(key="write_body").proto.placeholder)
        notice = "함께하실 분은 댓글로 참여 의사를 남겨주세요!"
        self.assertFalse(any(item.value == notice for item in self.at.markdown))
        self.at.radio(key="write_f_writetype").set_value("모임 모집").run()
        self.assertEqual(self.at.text_area(key="write_body").value.count("● 활동 내용 :"), 1)
        self.at.text_area(key="write_body").set_value("● 활동 내용 : 해변 청소").run()
        self.at.radio(key="write_f_writetype").set_value("실천 공유").run()
        self.assertEqual(self.at.text_area(key="write_body").value, "● 활동 내용 : 해변 청소")

    def test_other_board_or_inactive_writetype_keeps_default_placeholder(self):
        self.environment_board()
        self.board["id"] = "public"
        self.enter()
        self.at.radio(key="write_f_writetype").set_value("실천공유").run()
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "무엇에 대한 글인가요?")
        self.assertIn("사람 얼굴이나 차 번호판", self.at.text_area(key="write_body").proto.placeholder)
        self.leave()
        self.board["id"] = "environment"
        self.board["fields"][0]["active"] = False
        self.return_to_write()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.text_input(key="write_title").proto.placeholder, "무엇에 대한 글인가요?")
        self.assertFalse([r for r in self.at.radio if r.key == "write_f_writetype"])


if __name__ == "__main__":
    unittest.main()
