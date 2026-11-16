"""차유진 담당: 최근 글 권한·재수정·소수 입력 회귀 검사.

실제 DB 대신 메모리 DB와 모의 서비스를 사용해요.
    python -m unittest tools.test_community_improvements
"""
# 2026.11.12 - yujin -"최근 글 정보 노출과 재수정 입력값·소수 입력 문제의 재발을 검사해요."
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest
from sqlmodel import Session

from backend.routers import community
from components import fields
from services.api import ApiError
from tools import test_community_replies


ROOT = Path(__file__).resolve().parents[1]


class RecentPermissionTests(unittest.TestCase):
    def setUp(self):
        fixture = test_community_replies.ReplyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.client, self.engine = fixture.client, fixture.engine
        with Session(self.engine) as session:
            session.add(community.Board(id="obs", name="관찰 이야기", status="운영 중"))
            session.commit()
            for i in range(3):
                session.add(community.Post(id=f"obs{i}", board_id="obs", user_id="author",
                                           title=f"관찰 {i}", body=f"관찰 본문 {i}",
                                           created_at=datetime(2026, 11, 12, tzinfo=timezone.utc) + timedelta(minutes=i)))
            session.commit()

    def recent(self, user="guest", **params):
        response = self.client.get("/posts/recent", headers={"x-test-user": user}, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def configure(self, **changes):
        with Session(self.engine) as session:
            board = session.get(community.Board, "obs")
            for key, value in changes.items():
                setattr(board, key, value)
            session.add(board)
            session.commit()

    def test_read_and_list_permissions_for_each_viewer(self):
        for key in ("list", "read"):
            for level in ("모두", "회원", "관리자"):
                self.configure(perm={"list": "모두", "read": "모두", key: level})
                for user in ("guest", "member", "author", "admin"):
                    with self.subTest(key=key, level=level, user=user):
                        allowed = level == "모두" or user == "admin" or (level == "회원" and user != "guest")
                        self.assertEqual(len(self.recent(user)), 3 if allowed else 0)

    def test_inactive_boards_do_not_appear_in_home_feed(self):
        for status in ("준비 중", "일시 중지", "보관"):
            self.configure(status=status)
            for user in ("guest", "member", "admin"):
                with self.subTest(status=status, user=user):
                    self.assertEqual(self.recent(user), [])

    def test_missing_board_returns_empty_list(self):
        with Session(self.engine) as session:
            for i in range(3):
                session.delete(session.get(community.Post, f"obs{i}"))
            session.commit()
            session.delete(session.get(community.Board, "obs"))
            session.commit()
        self.assertEqual(self.recent(), [])

    def test_order_limit_hidden_and_deleted_posts(self):
        self.assertEqual([p["id"] for p in self.recent(n=2)], ["obs2", "obs1"])
        with Session(self.engine) as session:
            session.get(community.Post, "obs2").hidden = True
            session.get(community.Post, "obs1").deleted = True
            session.commit()
        for user in ("guest", "admin"):
            self.assertEqual([p["id"] for p in self.recent(user)], ["obs0"])


class NumberInputTests(unittest.TestCase):
    def test_fractional_values_with_integer_fractional_and_unbounded_ranges(self):
        for bounds in ({"min": 0, "max": 40}, {"min": 0.1, "max": 40.5}, {}):
            with self.subTest(bounds=bounds):
                definition = {"key": "depth", "label": "수심", "type": "number", **bounds}
                source = ("from components import fields\n"
                          f"fields.render([{definition!r}], 'number')\n")
                at = AppTest.from_string(source).run()
                self.assertFalse(at.exception)
                self.assertIsNone(at.number_input[0].value)
                at.number_input[0].set_value(5.5).run()
                self.assertFalse(at.exception)
                self.assertEqual(at.number_input[0].value, 5.5)
                self.assertEqual(fields.validate([definition], {"depth": 5.5}), {})
                self.assertEqual(fields.to_store([definition], {"depth": 5.5}), {"depth": 5.5})

    def test_existing_fraction_is_not_truncated(self):
        at = AppTest.from_string("""
from components import fields
fields.render([{'key': 'depth', 'label': '수심', 'type': 'number', 'min': 0, 'max': 40}],
              'number', values={'depth': 5.55})
""").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.number_input[0].value, 5.55)


class WriteReentryTests(unittest.TestCase):
    def test_save_reopen_preserves_saved_values_and_rerun_preserves_draft(self):
        definition = {"key": "depth", "label": "수심", "type": "number", "min": 0, "max": 40}
        board = {"id": "public", "name": "자유 수다", "desc": "", "type": "일반형",
                 "fields": [definition], "perm": {"photo": "관리자"}}
        post = {"id": "p1", "user": "author", "board": "public", "title": "원래 제목",
                "body": "원래 본문", "extra": {"depth": 5.5}}

        # 2026.11.12 - yujin -"공개 범위가 추가된 수정 요청도 기존 재수정 회귀 검사에 포함해요."
        def update(pid, title, body, extra, *args, **kwargs):
            post.update(title=title, body=body, extra=extra)
            return deepcopy(post)

        def go(*args, **kwargs):
            st.session_state["_test_detail"] = True
            st.rerun()

        source = f"""
import streamlit as st
from runpy import run_path
if st.session_state.get('_test_detail'):
    if st.button('글 수정'):
        st.session_state['_test_detail'] = False
        st.rerun()
else:
    run_path({str(ROOT / 'views/write.py')!r})
"""
        with ExitStack() as stack:
            overrides = {
                "services.auth.current_user": lambda: {"id": "author", "role": "user"},
                "services.community_data.get_post": lambda pid: (deepcopy(post), None),
                "services.community_data.update_post": update,
                "services.db.board": lambda bid: deepcopy(board),
                "services.db.boards": lambda **kw: [deepcopy(board)],
                "components.nav.go": go,
                "components.nav.link": lambda *args, **kw: None,
            }
            for target, value in overrides.items():
                stack.enter_context(patch(target, side_effect=value))
            at = AppTest.from_string(source)
            at.query_params["edit"] = "p1"
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(at.text_input[0].value, "원래 제목")
            self.assertEqual(at.number_input[0].value, 5.5)
            at.text_input[0].set_value("수정한 제목")
            at.text_area[0].set_value("수정한 본문")
            at.number_input[0].set_value(6.5)
            at.button[0].click().run()
            self.assertFalse(at.exception)
            self.assertEqual(post["title"], "수정한 제목")
            self.assertEqual(post["body"], "수정한 본문")
            self.assertEqual(post["extra"], {"depth": 6.5})
            # 상세로 나간 뒤 같은 글의 수정 화면에 다시 들어오는 실행을 재현해요.
            at.button[0].click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.text_input[0].value, "수정한 제목")
            self.assertEqual(at.text_area[0].value, "수정한 본문")
            self.assertEqual(at.number_input[0].value, 6.5)
            # 폼 입력은 제출 시 서버 상태에 전달되므로, 저장 실패 후 재실행에서도 초안을 확인해요.
            with patch("services.community_data.update_post", side_effect=ApiError(0, "통신 오류")):
                at.text_input[0].set_value("저장 전 초안")
                at.button[0].click().run()
                at.run()
            self.assertFalse(at.exception)
            self.assertEqual(at.text_input[0].value, "저장 전 초안")


if __name__ == "__main__":
    unittest.main()
