"""차유진 담당: U-08 목록 행과 게시글 조회수 검사. 실제 DB·Redis 값을 변경하지 않아요."""
# 2026.11.12 - yujin -"목록 열 정렬·상세 이동·조회수 중복 방지·접근 권한을 회귀 검사해요."
import ast
from contextlib import ExitStack
from pathlib import Path
import unittest
from unittest.mock import patch

from sqlmodel import Session
from streamlit.testing.v1 import AppTest

from backend.routers import community
from tools import test_community_replies


class PostViewCountTests(unittest.TestCase):
    def setUp(self):
        fixture = test_community_replies.ReplyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.client, self.engine = fixture.client, fixture.engine
        self.counts, self.seen = {}, set()

        def count(kind, pid, viewer):
            key = (kind, pid, viewer)
            if key in self.seen:
                return False
            self.seen.add(key)
            self.counts[pid] = self.counts.get(pid, 0) + 1
            return True

        self.count_patch = patch.object(community.redis_store, "view_once", side_effect=count)
        self.count = self.count_patch.start()
        self.addCleanup(self.count_patch.stop)
        reads = patch.object(community.redis_store, "views", side_effect=lambda kind, pid: self.counts.get(pid, 0))
        reads.start()
        self.addCleanup(reads.stop)

    def test_list_reads_do_not_count_and_detail_view_deduplicates(self):
        self.assertEqual(self.client.get("/posts").json()[0]["views"], 0)
        self.client.get("/posts/p1")
        self.count.assert_not_called()
        for _ in range(2):
            response = self.client.post("/posts/p1/view", headers={"x-test-user": "member"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["views"], 1)
        self.assertEqual(self.client.post("/posts/p1/view", headers={"x-test-user": "admin"}).json()["views"], 2)
        self.assertEqual(self.client.get("/posts/p1").json()["views"], 2)
        self.assertEqual(self.client.get("/posts/p2").json()["views"], 0)

    def test_guest_views_are_separated_by_guest_id(self):
        for guest, expected in (("one", 1), ("one", 1), ("two", 2)):
            response = self.client.post("/posts/p1/view", headers={"x-guest-id": guest})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["views"], expected)

    def test_inaccessible_posts_cannot_be_counted(self):
        with Session(self.engine) as session:
            b = session.get(community.Board, "public")
            b.perm = {"read": "관리자"}
            session.add(b)
            session.commit()
        self.assertEqual(self.client.post("/posts/p1/view").status_code, 403)
        self.assertEqual(self.client.post("/posts/missing/view").status_code, 404)
        self.count.assert_not_called()

    def test_redis_failure_keeps_posts_readable(self):
        with patch.object(community.redis_store, "views", side_effect=RuntimeError("offline")), \
                patch.object(community.redis_store, "view_once", side_effect=RuntimeError("offline")):
            response = self.client.get("/posts/p1")
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(response.json()["views"])
            self.assertEqual(self.client.post("/posts/p1/view").status_code, 503)

    # 2026.11.12 - yujin -"페이지 개수에 접근 불가·숨김·삭제 글이 포함되지 않고 검색 조건을 따르는지 확인해요."
    def test_count_matches_visible_search_results(self):
        self.assertEqual(self.client.get("/posts/count", params={"board": "public"}).json(), {"total": 2})
        self.assertEqual(self.client.get("/posts/count", params={"board": "public", "q": "p1"}).json(), {"total": 1})
        with Session(self.engine) as session:
            session.get(community.Post, "p1").hidden = True
            session.get(community.Post, "p2").deleted = True
            session.commit()
        self.assertEqual(self.client.get("/posts/count", params={"board": "public"}).json(), {"total": 0})
        with Session(self.engine) as session:
            session.get(community.Post, "p2").deleted = False
            board = session.get(community.Board, "public")
            board.perm = {"read": "관리자"}
            session.add(board)
            session.commit()
        self.assertEqual(self.client.get("/posts/count", params={"board": "public"}).json(), {"total": 0})
        self.assertEqual(self.client.get("/posts/count", params={"board": "public"},
                                         headers={"x-test-user": "admin"}).json(), {"total": 1})


class ListRowTests(unittest.TestCase):
    # 2026.11.13 - yujin -"반응형 관찰 그리드가 마지막 행의 글까지 빠짐없이 표시하고 상세 이동을 유지하는지 검사해요."
    def test_responsive_observation_grid_keeps_all_posts_and_navigation(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "views/community.py").read_text(encoding="utf-8"))
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
                 {"observation_grid", "post_created", "post_click_target", "open_post"}]
        source = """import html, base64
from datetime import datetime, timedelta, timezone
import streamlit as st
from components import ui, nav
from services import db, community_data as cd
def author_of(p):
    return {'nickname':'유진','character':'🐢'}
def photo_src(p):
    return None
""" + ast.unparse(ast.Module(body=funcs, type_ignores=[])) + """
observation_grid([{'id':f'p{i}','title':f'관찰 글 {i} <사진>','comments':i,'likes':i} for i in range(7)],{'id':'obs'})
"""
        with patch('services.db.species', return_value=None), patch('components.nav.go') as go, \
                patch('components.nav.remember_here'):
            at = AppTest.from_string(source).run()
            self.assertFalse(at.exception)
            self.assertEqual(len([b for b in at.button if str(b.key).startswith('community_open_')]), 7)
            self.assertIn('&lt;사진&gt;', '\n'.join(item.proto.body for item in at.get('html')))
            at.button(key='community_open_p6').click().run()
            go.assert_called_with('community', post='p6')

    def test_four_columns_escape_text_and_preserve_click_navigation(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / "views/community.py").read_text(encoding="utf-8"))
        functions = [n for n in source.body if isinstance(n, ast.FunctionDef)
                     and n.name in ("esc", "write_type_badge", "post_created", "list_rows", "post_click_target", "open_post")]
        app = """import html
from datetime import datetime, timedelta, timezone
import streamlit as st
from services import community_data as cd
from components import nav
def author_of(p):
    return p['author']
""" + ast.unparse(ast.Module(body=functions, type_ignores=[])) + """
list_rows([
    {'id':'p1', 'title':'긴 제목 <script>alert(1)</script>', 'author':{'nickname':'바다 <친구>'},
     'created_at':'2026-11-12T10:00:00+09:00', 'views':1234, 'comments':3},
    {'id':'p2', 'title':'다음 글', 'author':{'nickname':'유진'}, 'created_at':'2026-11-11T10:00:00+09:00', 'views':None, 'comments':0}
], {'id':'public'})
"""
        with ExitStack() as stack:
            go = stack.enter_context(patch("components.nav.go"))
            remember = stack.enter_context(patch("components.nav.remember_here"))
            at = AppTest.from_string(app).run()
            self.assertFalse(at.exception)
            rendered = "\n".join(x.value for x in at.markdown)
            for value in ("제목", "작성자", "작성일", "조회수", "2026.11.12", "1,234", "—",
                          "&lt;script&gt;", "바다 &lt;친구&gt;"):
                self.assertIn(value, rendered)
            self.assertNotIn("<script>", rendered)
            # 2026.11.12 - yujin -"댓글이 있는 행에만 제목 옆 댓글 수 배지가 표시되는지 확인해요."
            post_rows = [x.value for x in at.markdown if 'class="community-list-title"' in x.value]
            self.assertIn('class="community-list-comments">댓글 3</span>', post_rows[0])
            self.assertNotIn('class="community-list-comments"', post_rows[1])
            at.button[0].click().run()
            self.assertFalse(at.exception)
            remember.assert_called_with("community", board="public")
            go.assert_called_with("community", post="p1")

    # 2026.11.12 - yujin -"20개 단위 페이지 이동과 검색 시 첫 페이지 복귀를 실제 화면 실행으로 검사해요."
    def test_pages_are_capped_at_twenty_and_search_resets_page(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / "views/community.py").read_text(encoding="utf-8"))
        names = {"esc", "write_type_badge", "post_created", "list_rows", "show_list", "write_button", "post_click_target", "open_post"}
        functions = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
        app = """import html
from datetime import datetime, timedelta, timezone
import streamlit as st
from services import community_data as cd, db
from services.api import ApiError
from components import nav, ui, auth_gate
S = st.session_state
user, admin = None, False
CONTROL_COLUMNS = [3, 1.3]
def author_of(p):
    return p['author']
""" + ast.unparse(ast.Module(body=functions, type_ignores=[])) + "\nshow_list()\n"
        board = {"id": "qna", "name": "궁금해요", "type": "질문형", "layout": "list", "page_size": 50,
                 "perm": {"read": "모두", "write": "회원"}, "desc": "궁금한 것을 물어봐요"}
        posts = [{"id": f"p{i}", "title": f"질문 {i}", "author": {"nickname": "유진"},
                  "created_at": "2026-11-12T10:00:00+09:00", "views": i} for i in range(45)]

        def listing(b, q, sort, *, limit, offset):
            matches = [p for p in posts if q in p["title"]]
            return matches[offset:offset + limit]

        with patch("services.community_data.visible_boards", return_value=[board]), \
                patch("services.db.board", return_value=board), \
                patch("services.community_data.count_visible_posts", side_effect=lambda b, q: len([p for p in posts if q in p["title"]])), \
                patch("services.community_data.list_posts", side_effect=listing) as listing_mock:
            at = AppTest.from_string(app).run()
            self.assertFalse(at.exception)
            row_buttons = lambda: [b for b in at.button if str(b.key).startswith("community_open_")]
            self.assertEqual(len(row_buttons()), 20)
            self.assertTrue(at.button(key="community_previous").disabled)
            # 2026.11.12 - yujin -"페이지 번호를 눌러 중간 페이지를 거치지 않고 바로 이동해요."
            self.assertIn("현재 페이지", at.button(key="community_page_number_0").proto.help)
            at.button(key="community_page_number_2").click().run()
            # 2026.11.12 - yujin -"페이지 번호로 이동하면 목록 상단 스크롤 스크립트를 한 번 실행하는지 확인해요."
            self.assertTrue(any("scrollIntoView" in x.proto.body for x in at.get("html")))
            self.assertEqual(len(row_buttons()), 5)
            self.assertEqual(row_buttons()[0].key, "community_open_p40")
            at.button(key="community_page_number_0").click().run()
            at.button(key="community_next").click().run()
            self.assertFalse(at.exception)
            self.assertEqual(len(row_buttons()), 20)
            self.assertEqual(row_buttons()[0].key, "community_open_p20")
            at.button(key="community_next").click().run()
            self.assertEqual(len(row_buttons()), 5)
            self.assertTrue(at.button(key="community_next").disabled)
            at.button(key="community_previous").click().run()
            self.assertEqual(row_buttons()[0].key, "community_open_p20")
            at.text_input(key="community_q").set_value("질문 1").run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state["community_page_qna"], 0)
            self.assertEqual(len(row_buttons()), 11)
            self.assertTrue(all(call.kwargs["limit"] <= 21 for call in listing_mock.call_args_list))
            posts.extend({"id": f"p{i}", "title": f"질문 {i}", "author": {"nickname": "유진"},
                          "created_at": "2026-11-12T10:00:00+09:00", "views": i} for i in range(45, 225))
            at.text_input(key="community_q").set_value("").run()
            numbers = lambda: [b for b in at.button if str(b.key).startswith("community_page_number_")]
            self.assertEqual(len(numbers()), 10)
            at.button(key="community_page_number_9").click().run()
            at.button(key="community_next").click().run()
            self.assertEqual([b.label for b in numbers()], ["11", "12"])
            at.button(key="community_page_number_11").click().run()
            self.assertEqual(len(row_buttons()), 5)
            self.assertTrue(at.button(key="community_next").disabled)
            del posts[1:]
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state["community_page_qna"], 0)
            self.assertEqual(len(row_buttons()), 1)
            self.assertEqual(numbers(), [])

    # 2026.11.13 - yujin -"유형 라벨의 띄어쓰기 호환·다른 게시판 제외·잘못된 값 제외와 제목 앞 배치를 검사해요."
    def test_environment_type_badges_precede_titles_and_keep_comments(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / "views/community.py").read_text(encoding="utf-8"))
        names = {"esc", "write_type_badge", "post_created", "list_rows", "post_click_target", "open_post"}
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        code = ast.unparse(ast.Module(body=funcs, type_ignores=[]))
        env = {}
        exec(compile(ast.Module(body=[n for n in funcs if n.name == "write_type_badge"], type_ignores=[]), "badge", "exec"), env)
        badge = env["write_type_badge"]
        for value in ("실천 공유", "실천공유", "모임 모집", "모임모집"):
            self.assertIn('border-radius:999px', badge({"extra": {"writetype": value}}, {"id": "environment"}))
            self.assertEqual(badge({"extra": {"writetype": value}}, {"id": "qna"}), "")
        for extra in ({}, {"writetype": None}, {"writetype": []}, {"writetype": '<script>alert(1)</script>'}):
            self.assertEqual(badge({"extra": extra}, {"id": "environment"}), "")
        app = """import html
from datetime import datetime, timedelta, timezone
import streamlit as st
from services import community_data as cd
from components import nav
def author_of(p):
    return {'nickname':'유진'}
""" + code + """
list_rows([
    {'id':'practice','title':'해변 청소 <후기>','extra':{'writetype':'실천 공유'},'comments':2},
    {'id':'meeting','title':'청소 모임','extra':{'writetype':'모임 모집'},'comments':0},
    {'id':'legacy','title':'예전 글','extra':{},'comments':0}
], {'id':'environment'})
"""
        at = AppTest.from_string(app).run()
        self.assertFalse(at.exception)
        rows = [m.value for m in at.markdown if 'class="community-list-title"' in m.value]
        self.assertLess(rows[0].index('실천 공유'), rows[0].index('class="community-list-title-text"'))
        self.assertIn('댓글 2', rows[0])
        self.assertIn('&lt;후기&gt;', rows[0])
        self.assertLess(rows[1].index('모임 모집'), rows[1].index('class="community-list-title-text"'))
        self.assertNotIn('community-write-type', rows[2])


if __name__ == "__main__":
    unittest.main()
