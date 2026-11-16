"""2026.11.13 sunghoon 관리자 권한 부여와 로그인 동기화 검증."""
from datetime import timedelta
from types import SimpleNamespace
import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from backend import auth_provider
from backend.core import security
from backend.core.timefmt import now
from backend.db import get_session
from backend.models import AdminLog, User
from backend.routers import admin


class AdminRoleGrantTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        SQLModel.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            session.add_all([User(id='admin', email='admin@example.test', nickname='admin', role='admin', email_verified=True),
                             User(id='member', email='member@example.test', nickname='member', email_verified=True),
                             User(id='blocked', email='blocked@example.test', nickname='blocked', email_verified=True,
                                  suspended_until=now() + timedelta(days=1))])
            session.commit()
        app = FastAPI()
        app.include_router(admin.router)

        def db():
            with Session(self.engine) as session:
                yield session

        def viewer(request: Request):
            with Session(self.engine) as session:
                return session.get(User, request.headers.get('x-test-user', ''))

        app.dependency_overrides[get_session] = db
        app.dependency_overrides[security.optional_user] = viewer
        self.client = TestClient(app)

    def post(self, target, actor='admin'):
        return self.client.post(f'/admin/members/{target}/grant-admin', headers={'x-test-user': actor})

    def test_only_admin_can_grant_and_relogin_preserves_grant(self):
        self.assertEqual(self.post('member', 'member').status_code, 403)
        self.assertEqual(self.post('member').status_code, 200)
        self.assertEqual(self.post('member').status_code, 409)
        with Session(self.engine) as session:
            audit = session.exec(select(AdminLog).where(AdminLog.target == 'member')).one()
            self.assertEqual(audit.action, '관리자 권한 부여')
            sb_user = SimpleNamespace(id='member', email='member@example.test', user_metadata={}, app_metadata={})
            self.assertEqual(auth_provider.sync_supabase_user(session, sb_user).role, 'admin')

    def test_suspended_or_missing_member_is_rejected(self):
        self.assertEqual(self.post('blocked').status_code, 400)
        self.assertEqual(self.post('missing').status_code, 404)

    def test_existing_external_admin_still_follows_external_role(self):
        with Session(self.engine) as session:
            user = session.get(User, 'member')
            user.role = 'admin'
            session.add(user)
            session.commit()
            sb_user = SimpleNamespace(id='member', email='member@example.test', user_metadata={}, app_metadata={})
            self.assertEqual(auth_provider.sync_supabase_user(session, sb_user).role, 'user')


if __name__ == '__main__':
    unittest.main()
