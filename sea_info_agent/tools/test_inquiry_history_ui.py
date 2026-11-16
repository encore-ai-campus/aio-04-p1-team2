"""문의 공개 이력과 관리자 알림 제어의 화면 렌더링 검증."""
from pathlib import Path
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
ITEM = {"id":"R-TEST", "type":"기능 건의", "title":"화면 시험", "body":"시험 본문", "status":"처리 중",
        "date":"2026-11-12", "sender":"시험자", "jira_key":"KAN-TEST", "jira_status":"created", "answer":"",
        "jira_url":"", "mail_sent":False,
        "history":[{"id":"e1", "version":1, "kind":"started", "status":"처리 중", "message":"처리를 시작했어요.", "created_at":"2026-11-12T18:00:00+09:00"}],
        "notifications":[{"id":"n1", "kind":"started", "status":"unknown", "attempts":1, "created_at":"2026-11-12T18:00:00+09:00", "sent_at":""}]}


class HistoryUITests(unittest.TestCase):
    def test_public_history_renders(self):
        at = AppTest.from_string("from components.inquiry_history import render\nrender("+repr(ITEM)+")").run()
        self.assertFalse(at.exception)
        self.assertTrue(any("처리 이력" in x.label for x in at.expander))
        self.assertTrue(any("처리를 시작" in x.value for x in at.markdown))

    def test_empty_history_does_not_show_fake_events(self):
        at = AppTest.from_string("from components.inquiry_history import render\nrender({})").run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.expander), 0)

    def test_admin_jira_authority_and_unknown_retry_confirmation(self):
        with patch('services.auth.current_user', return_value={"id":"admin", "role":"admin"}), \
             patch('services.auth.health', return_value={"modes":{"jira":"on"}}), \
             patch('services.inquiry_data.admin_list', return_value=[ITEM]):
            at = AppTest.from_file(str(ROOT/'views/admin/inquiries.py'), default_timeout=15)
            at.query_params['shot']='detail'
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(at.selectbox(key='ainq_state_R-TEST_0').disabled)
            self.assertTrue(at.button(key='inq_mail_retry_n1').disabled)
            at.checkbox(key='inq_mail_checked_n1').check().run()
            self.assertFalse(at.exception)
            self.assertFalse(at.button(key='inq_mail_retry_n1').disabled)

    def test_review_pending_direct_choice_refreshes_status_and_enables_reply(self):
        item = {**ITEM, "jira_key":"", "jira_status":"not_registered", "status":"접수",
                "handling_route":"undecided", "review_version":0, "notifications":[]}
        def choose(rid, route, note, version):
            item.update(handling_route='direct',status='처리 중',review_version=1)
            return {'ok':True,'message':'직접 처리 선택'}
        with patch('services.auth.current_user',return_value={'id':'admin','role':'admin'}), \
             patch('services.auth.health',return_value={'modes':{'jira':'on'}}), \
             patch('services.inquiry_data.admin_list',side_effect=lambda:[dict(item)]), \
             patch('services.inquiry_data.review',side_effect=choose) as review:
            at=AppTest.from_file(str(ROOT/'views/admin/inquiries.py'),default_timeout=15)
            at.query_params['shot']='detail'
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(at.button(key='ainq_save_R-TEST').disabled)
            self.assertFalse(any(button.key == 'ainq_jira_create_R-TEST' for button in at.button))
            at.button(key='ainq_direct_R-TEST').click().run()
            self.assertFalse(at.exception)
            review.assert_called_once()
            self.assertEqual(at.selectbox(key='ainq_state_R-TEST_1').value,'처리 중')
            self.assertTrue(at.button(key='ainq_save_R-TEST').disabled)
            at.selectbox(key='ainq_state_R-TEST_1').select('완료').run()
            self.assertTrue(at.button(key='ainq_save_R-TEST').disabled)
            at.text_area(key='ainq_answer_R-TEST_1').input('처리 결과를 안내해요.').run()
            self.assertFalse(at.button(key='ainq_save_R-TEST').disabled)

    def test_jira_creation_requires_admin_preview_confirmation(self):
        item={**ITEM,'jira_key':'','jira_status':'not_registered','status':'접수','notifications':[]}
        with patch('services.auth.current_user',return_value={'id':'admin','role':'admin'}), \
             patch('services.auth.health',return_value={'modes':{'jira':'on'}}), \
             patch('services.inquiry_data.admin_list',return_value=[item]), \
             patch('services.inquiry_data.jira_create',return_value={'ok':True,'message':'등록'}) as create:
            at=AppTest.from_file(str(ROOT/'views/admin/inquiries.py'),default_timeout=15)
            at.query_params['shot']='detail'
            at.run()
            at.radio(key='ainq_method_R-TEST').set_value('Jira 이슈 생성').run()
            self.assertTrue(at.selectbox(key='ainq_state_R-TEST_0').disabled)
            self.assertTrue(at.button(key='ainq_jira_create_R-TEST').disabled)
            at.checkbox(key='ainq_jira_approve_R-TEST').check().run()
            at.button(key='ainq_jira_create_R-TEST').click().run()
            self.assertFalse(at.exception)
            create.assert_called_once()

    def test_jira_choice_locks_status_before_issue_creation(self):
        item={**ITEM, 'jira_key':'', 'jira_status':'not_registered', 'status':'처리 중',
              'handling_route':'direct', 'notifications':[]}
        with patch('services.auth.current_user',return_value={'id':'admin','role':'admin'}), \
             patch('services.auth.health',return_value={'modes':{'jira':'on'}}), \
             patch('services.inquiry_data.admin_list',return_value=[item]):
            at=AppTest.from_file(str(ROOT/'views/admin/inquiries.py'),default_timeout=15)
            at.query_params['shot']='detail'
            at.run()
            self.assertFalse(at.selectbox(key='ainq_state_R-TEST_0').disabled)
            at.radio(key='ainq_method_R-TEST').set_value('Jira 이슈 생성').run()
            self.assertFalse(at.exception)
            self.assertTrue(at.selectbox(key='ainq_state_R-TEST_0').disabled)
            self.assertTrue(at.button(key='ainq_save_R-TEST').disabled)
            self.assertFalse(any('관리자 검토 후 선택 등록' in x.value for x in at.markdown))
