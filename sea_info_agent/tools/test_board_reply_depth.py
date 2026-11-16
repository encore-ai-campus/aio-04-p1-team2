"""차유진 담당: 게시판 관리 저장과 실제 답글 제한 연동 검사. 메모리 DB만 사용해요."""
# 2026.11.13 - yujin -"관리자 저장·범위 검증·게시판별 제한·기존 답글 보존·기본값 호환을 검사해요."
import unittest
import ast
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request
from sqlmodel import Session, select

from backend.routers import admin_boards, community
from backend.schemas.community import RulesIn, reply_depth_limit
from services import community_data as cd
from tools import test_community_replies
from streamlit.testing.v1 import AppTest


class BoardReplyDepthTests(unittest.TestCase):
    def setUp(self):
        fixture = test_community_replies.ReplyTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.client, self.engine = fixture.client, fixture.engine
        self.client.app.include_router(admin_boards.router)

        def admin(request: Request):
            if request.headers.get('x-test-user') != 'admin':
                raise HTTPException(403, '관리자만 할 수 있어요.')
            return fixture.users['admin']

        self.client.app.dependency_overrides[admin_boards.require_admin] = admin

    def configure(self, depth, user='admin'):
        return self.client.patch('/admin/boards/public', json={'rules': {'reply_depth': depth}},
                                 headers={'x-test-user': user})

    def test_saved_limit_above_five_enforced_and_lowered_limit_preserves_old_threads(self):
        with Session(self.engine) as session:
            b = session.get(community.Board, 'public')
            b.rules = {'comments': True, 'likes': False, 'interval': 90}
            session.add(b)
            session.commit()
        result = self.configure(7)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['rules']['reply_depth'], 7)
        self.assertFalse(result.json()['rules']['likes'])
        self.assertEqual(result.json()['rules']['interval'], 90)
        with Session(self.engine) as session:
            self.assertEqual(session.get(community.Board, 'public').rules['reply_depth'], 7)
            logs = session.exec(select(community.AdminLog)).all()
            self.assertTrue(any('답글 5단계' in row.before and '답글 7단계' in row.after for row in logs))
        parent = 'legacy'
        for depth in range(1, 8):
            r = self.fixture.add(parent=parent)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()['depth'], depth)
            parent = r.json()['id']
        self.assertEqual(self.fixture.add(parent=parent).status_code, 409)
        self.assertEqual(self.configure(2).status_code, 200)
        self.assertEqual([c['depth'] for c in self.fixture.comments()], list(range(8)))
        before = self.fixture.count()
        self.assertEqual(self.fixture.add(parent=parent).status_code, 409)
        self.assertEqual(self.fixture.count(), before)
        self.assertEqual(self.fixture.add(parent='legacy').status_code, 200)

    def test_zero_only_blocks_replies_and_other_board_keeps_default(self):
        self.assertEqual(self.configure(0).status_code, 200)
        self.assertEqual(self.fixture.add().status_code, 409)
        self.assertEqual(self.fixture.add(parent=None).status_code, 200)
        with Session(self.engine) as session:
            session.add(community.Board(id='other', name='다른 게시판', status='운영 중'))
            session.commit()
            session.get(community.Post, 'p2').board_id = 'other'
            session.commit()
        root = self.fixture.add(post='p2', parent=None).json()['id']
        self.assertEqual(self.fixture.add(post='p2', parent=root).status_code, 200)

    def test_invalid_values_and_non_admin_cannot_update(self):
        for value in (-1, 21, 1.5, True, '3'):
            with self.subTest(value=value):
                self.assertEqual(self.configure(value).status_code, 422)
        self.assertEqual(self.configure(2, user='member').status_code, 403)
        with Session(self.engine) as session:
            self.assertEqual(reply_depth_limit(session.get(community.Board, 'public').rules), 5)

    def test_legacy_defaults_form_payload_and_zero_value(self):
        self.assertEqual(RulesIn().reply_depth, 5)
        self.assertEqual(cd.opt({'rules': {}}, 'rules')['reply_depth'], 5)
        for depth in (0, 2, 20):
            payload = cd.board_payload({'reply_depth': depth}, is_new=False)
            self.assertEqual(payload['rules']['reply_depth'], depth)
            self.assertEqual(cd.opt({'rules': payload['rules']}, 'rules')['reply_depth'], depth)

    # 2026.11.13 - yujin -"실제 운영 규칙 입력칸에서 저장을 눌러 API와 DB에 깊이 값이 전달되는지 검사해요."
    def test_admin_form_saves_selected_depth_through_api(self):
        with Session(self.engine) as session:
            board = session.get(community.Board, 'public')
            board.perm = dict(community.DEFAULT_PERM)
            session.add(board)
            session.commit()
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'views/admin/boards.py').read_text(encoding='utf-8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('board_form', 'read_form', 'save_board')]
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id in ('FORM_ATTRS', 'FIELD_ERR_SHOWN', 'STATUS_HELP')
                               for target in node.targets)]
        app = '''import streamlit as st
from services import db, community_data as cd
from services.api import ApiError
from components import fields, ui
from backend.schemas import community as rules
S = st.session_state
me = {'nickname':'운영자'}
''' + ast.unparse(ast.Module(body=assignments + functions, type_ignores=[])) + "\nboard_form('test', db.board('public'), False)\n"

        def board(bid):
            response = self.client.get('/boards/' + bid)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        def update(bid, data):
            response = self.client.patch('/admin/boards/' + bid, json=data, headers={'x-test-user':'admin'})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        with patch('services.db.board', side_effect=board), patch('services.community_data.update_board', side_effect=update):
            at = AppTest.from_string(app).run()
            self.assertFalse(at.exception)
            self.assertEqual(at.number_input(key='test_reply_depth').value, 5)
            at.number_input(key='test_reply_depth').set_value(3)
            next(button for button in at.button if button.label == '설정 저장하기').click().run()
            self.assertFalse(at.exception)
            self.assertNotIn('boards_form_errs', at.session_state)
        with Session(self.engine) as session:
            self.assertEqual(session.get(community.Board, 'public').rules['reply_depth'], 3)


if __name__ == '__main__':
    unittest.main()
