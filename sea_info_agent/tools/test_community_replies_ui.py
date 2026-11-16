"""차유진 담당: 단계별 답글 버튼과 답글 제출 UI 검사. 실제 데이터는 바꾸지 않아요."""
# 2026.11.12 - yujin -"4단계 답글의 하위 답글 제출과 5단계·삭제·읽기 전용 버튼 제한을 확인해요."
import ast
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class NestedReplyUITests(unittest.TestCase):
    def test_nested_reply_form_and_depth_limit(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "views/community.py").read_text(encoding="utf-8"))
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {
            "esc", "post_created", "show_comments", "reply_form", "submit_reply", "del_comment"}]
        source = """import html
from datetime import datetime, timedelta, timezone
import streamlit as st
from services import community_data as cd, db
from services.api import ApiError
from components import ui, auth_gate
S = st.session_state
user = {'id':'member', 'role':'user'}
uid, admin = 'member', False
""" + ast.unparse(ast.Module(body=functions, type_ignores=[])) + """
show_comments({'id':'p1'}, {'name':'궁금해요', 'perm':{'comment':'회원'}, 'rules':{'reply_depth':S.get('_test_depth',5)}},
              read_only=S.get('_test_read_only', False))
"""
        comments = [{"id": f"c{i}", "post": "p1", "parent_id": f"c{i-1}" if i else None,
                     "depth": i, "body": f"{i}단계 내용", "user": "author", "author": {"nickname": "바다친구"},
                     "created_at": "2026-11-12T10:00:00+09:00", "deleted": False} for i in range(6)]
        with patch("services.community_data.comments_for", return_value=comments), \
                patch("services.community_data.add_comment", return_value={}) as add, \
                patch("components.auth_gate.login_required", return_value=True):
            at = AppTest.from_string(source).run()
            self.assertFalse(at.exception)
            replies = lambda: [b.key for b in at.button if str(b.key).startswith("community_reply_")]
            # 2026.11.12 - yujin -"기본 접힘과 답글 개수·펼치기 및 대상 이름 표시를 확인해요."
            self.assertEqual(replies(), ["community_reply_c0"])
            self.assertEqual(at.button(key="community_thread_toggle_c0").label, "답글 5개 보기")
            at.button(key="community_thread_toggle_c0").click().run()
            self.assertFalse(at.exception)
            self.assertIn("바다친구님에게 답글", "\n".join(x.proto.body for x in at.get("html")))
            self.assertEqual(replies(), [f"community_reply_c{i}" for i in range(5)])
            at.button(key="community_reply_c4").click().run()
            self.assertFalse(at.exception)
            at.text_area(key="community_reply_body_c4").set_value("5단계 답글이에요")
            next(b for b in at.button if b.label == "답글 달기").click().run()
            self.assertFalse(at.exception)
            add.assert_called_once_with("p1", "5단계 답글이에요", parent_id="c4")
            # 2026.11.13 - yujin -"게시판별 깊이를 바꾸면 버튼·안내가 갱신되고 기존 깊은 답글은 계속 보여요."
            for limit in (2, 7, 0, 5):
                at.session_state['_test_depth'] = limit
                at.run()
                self.assertFalse(at.exception)
                self.assertEqual(replies(), [f'community_reply_c{i}' for i in range(min(limit, 6))])
                self.assertIn('5단계 내용', '\n'.join(x.proto.body for x in at.get('html')))
            comments[2].update(deleted=True, body="", author=None, user="")
            at.run()
            self.assertFalse(at.exception)
            self.assertNotIn("community_reply_c2", replies())
            self.assertIn("community_reply_c3", replies())
            self.assertIn("삭제된 댓글에 대한 답글", "\n".join(x.proto.body for x in at.get("html")))
            self.assertEqual(at.button(key="community_thread_toggle_c0").label, "답글 4개 접기")
            # 접었다 펴도 입력 중인 답글을 보존하고, 읽기 전용 화면에서도 펼쳐 읽을 수 있어요.
            at.button(key="community_reply_c3").click().run()
            at.text_area(key="community_reply_body_c3").set_value("작성 중 초안")
            # 폼의 미제출 입력은 클라이언트에 있으므로 서버에 전달된 초안 상태를 재현해요.
            at.session_state["community_reply_body_c3"] = "작성 중 초안"
            at.button(key="community_thread_toggle_c0").click().run()
            self.assertEqual(replies(), ["community_reply_c0"])
            at.button(key="community_thread_toggle_c0").click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.text_area(key="community_reply_body_c3").value, "작성 중 초안")
            at.session_state["_test_read_only"] = True
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(replies(), [])
            at.button(key="community_thread_toggle_c0").click().run()
            at.button(key="community_thread_toggle_c0").click().run()
            self.assertFalse(at.exception)
            self.assertIn("삭제된 댓글에 대한 답글", "\n".join(x.proto.body for x in at.get("html")))
            # 2026.11.12 - yujin -"원댓글별 접힘 상태가 독립적이며 삭제된 원댓글에서도 하위 답글을 펼칠 수 있는지 확인해요."
            comments.extend([
                {"id": "other", "post": "p1", "parent_id": None, "depth": 0, "body": "다른 원댓글",
                 "user": "author", "author": {"nickname": "다른친구"}, "created_at": "2026-11-12T10:01:00+09:00"},
                {"id": "other-reply", "post": "p1", "parent_id": "other", "depth": 1, "body": "다른 답글",
                 "user": "author", "author": {"nickname": "답글친구"}, "created_at": "2026-11-12T10:02:00+09:00"},
            ])
            at.run()
            self.assertEqual(at.button(key="community_thread_toggle_other").label, "답글 1개 보기")
            at.button(key="community_thread_toggle_other").click().run()
            self.assertEqual(at.button(key="community_thread_toggle_c0").label, "답글 4개 접기")
            self.assertIn("다른친구님에게 답글", "\n".join(x.proto.body for x in at.get("html")))
            comments[0].update(deleted=True, body="", user="", author=None)
            with patch("services.db.user", side_effect=AssertionError("삭제된 댓글 작성자를 조회하면 안 돼요")):
                at.run()
                self.assertFalse(at.exception)
                self.assertEqual(at.button(key="community_thread_toggle_c0").label, "답글 4개 접기")
                self.assertIn("삭제된 댓글에 대한 답글", "\n".join(x.proto.body for x in at.get("html")))


if __name__ == "__main__":
    unittest.main()
