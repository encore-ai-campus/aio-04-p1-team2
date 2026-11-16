"""차유진 담당: 일반 게시글의 공개 범위 저장·조회·사진 접근 회귀 검사. 메모리 DB만 사용해요."""
# 2026.11.12 - yujin -"전체/멤버 공개와 게시판 권한의 결합, 기존 글 호환성, 관찰 글 범위 제외를 확인해요."
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from sqlmodel import Session, select

from backend.community_models import PostVisibility
from backend.routers import community, photos
from tools import test_community_replies
from services import community_data


class PostVisibilityTests(unittest.TestCase):
    def setUp(self):
        fixture = test_community_replies.ReplyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.client, self.engine = fixture.client, fixture.engine
        self.client.app.include_router(photos.router)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(community.redis_store, "gap_ok", return_value=True))
        self.stack.enter_context(patch.object(community.redis_store, "views", return_value=0))
        self.views = self.stack.enter_context(patch.object(community.redis_store, "view_once", return_value=True))

    def request(self, method, url, user="guest", **kw):
        return self.client.request(method, url, headers={"x-test-user": user}, **kw)

    def create(self, visibility="members"):
        response = self.request("POST", "/posts", "member", json={"board": "public", "title": "비공개 검증 제목",
                               "body": "멤버 전용 본문", "visibility": visibility})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def test_legacy_posts_remain_public_and_new_setting_is_persisted(self):
        legacy = self.request("GET", "/posts/p1")
        self.assertEqual(legacy.status_code, 200)
        self.assertEqual(legacy.json()["visibility"], "public")
        pid = self.create()
        with Session(self.engine) as session:
            self.assertEqual(session.get(PostVisibility, pid).visibility, "members")
        for user in ("member", "author", "admin"):
            response = self.request("GET", f"/posts/{pid}", user)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["visibility"], "members")

    def test_guest_list_search_count_detail_comments_and_views_are_restricted(self):
        pid = self.create()
        for url in (f"/posts/{pid}", f"/posts/{pid}/comments"):
            response = self.request("GET", url)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("멤버 전용 본문", response.text)
        self.assertEqual(self.request("POST", f"/posts/{pid}/view").status_code, 403)
        self.views.assert_not_called()
        for params in ({}, {"board": "public"}, {"board": "public", "q": "비공개"}):
            rows = self.request("GET", "/posts", params=params).json()
            self.assertNotIn(pid, [p["id"] for p in rows])
        self.assertEqual(self.request("GET", "/posts/count", params={"q": "비공개"}).json()["total"], 0)
        self.assertEqual(self.request("GET", "/posts/count", "author", params={"q": "비공개"}).json()["total"], 1)
        self.assertEqual(self.request("POST", f"/posts/{pid}/comments", "author", json={"body": "회원 댓글"}).status_code, 200)

    def test_update_preserves_omitted_visibility_and_changes_access(self):
        pid = self.create()
        self.assertEqual(self.request("PATCH", f"/posts/{pid}", "author", json={"visibility": "public"}).status_code, 403)
        response = self.request("PATCH", f"/posts/{pid}", "member", json={"title": "제목 수정"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["visibility"], "members")
        self.assertEqual(self.request("GET", f"/posts/{pid}").status_code, 403)
        self.assertEqual(self.request("PATCH", f"/posts/{pid}", "member", json={"visibility": "public"}).status_code, 200)
        self.assertEqual(self.request("GET", f"/posts/{pid}").status_code, 200)
        self.assertEqual(self.request("PATCH", f"/posts/{pid}", "member", json={"visibility": "members"}).status_code, 200)
        self.assertEqual(self.request("GET", f"/posts/{pid}").status_code, 403)

    def test_public_selection_cannot_override_board_read_permission(self):
        pid = self.create("public")
        with Session(self.engine) as session:
            b = session.get(community.Board, "public")
            b.perm = {"read": "관리자", "list": "모두"}
            session.add(b)
            session.commit()
        self.assertEqual(self.request("GET", f"/posts/{pid}").status_code, 403)
        self.assertEqual(self.request("GET", f"/posts/{pid}", "author").status_code, 403)
        self.assertEqual(self.request("GET", f"/posts/{pid}", "admin").status_code, 200)
        self.assertEqual(self.request("GET", "/posts/count", params={"board": "public"}).json()["total"], 0)

    def test_photo_route_uses_the_same_post_visibility(self):
        pid = self.create()
        filename = "pp_abcdefghij.jpg"
        with Session(self.engine) as session:
            session.get(community.Post, pid).photo_path = filename
            session.commit()
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, filename).write_bytes(b"image fixture")
            with patch.object(photos, "get_settings", return_value=SimpleNamespace(upload_dir=folder)):
                self.assertEqual(self.request("GET", f"/files/{filename}").status_code, 404)
                self.assertEqual(self.request("GET", f"/files/{filename}", "author").status_code, 200)

    def test_invalid_visibility_and_observation_scope(self):
        for value in ("private", "", 123):
            response = self.request("POST", "/posts", "member", json={"board": "public", "title": "제목",
                                    "body": "내용", "visibility": value})
            self.assertEqual(response.status_code, 422)
        with Session(self.engine) as session:
            session.add(community.Board(id="obs", name="관찰 이야기", status="운영 중"))
            session.commit()
            session.add(community.Post(id="obs1", board_id="obs", user_id="author", title="기존 관찰", body="관찰 내용"))
            session.commit()
        response = self.request("PATCH", "/posts/obs1", "author", json={"visibility": "members"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.request("GET", "/posts/obs1").status_code, 200)
        self.assertIn("obs1", [p["id"] for p in self.request("GET", "/posts/recent").json()])

    def test_failed_visibility_save_rolls_back_post_creation(self):
        with Session(self.engine) as session:
            before = len(session.exec(select(community.Post)).all())
        with patch.object(community, "save_visibility", side_effect=RuntimeError("test failure")):
            response = self.request("POST", "/posts", "member", json={"board": "public", "title": "제목",
                                    "body": "내용", "visibility": "members"})
            self.assertEqual(response.status_code, 500)
        with Session(self.engine) as session:
            self.assertEqual(len(session.exec(select(community.Post)).all()), before)

    def test_service_helpers_send_visibility_and_preserve_omitted_patch(self):
        with patch.object(community_data, "_call", return_value={"id": "created"}) as call, \
                patch.object(community_data, "_changed", side_effect=lambda result: result):
            community_data.create_post("public", "제목", "내용", {}, visibility="members")
            self.assertEqual(call.call_args.kwargs["json"]["visibility"], "members")
            community_data.update_post("p1", "제목", "내용", {})
            self.assertNotIn("visibility", call.call_args.kwargs["json"])
            community_data.update_post("p1", "제목", "내용", {}, visibility="public")
            self.assertEqual(call.call_args.kwargs["json"]["visibility"], "public")


if __name__ == "__main__":
    unittest.main()
