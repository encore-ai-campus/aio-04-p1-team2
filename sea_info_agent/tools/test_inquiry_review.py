"""관리자 검토 후 처리·Jira 생성·권한·메일 회귀. 외부 발송이나 이슈 생성 없음."""
from datetime import timedelta
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
from sqlmodel import Session, select
from backend.core import jira
from backend.core.timefmt import now
from backend.inquiry_workflow_models import InquiryReview
from backend.models import Inquiry, AdminLog
from backend import inquiry_workflow as flow, inquiry_worker
from tools import test_inquiry_notifications as base


class ReviewTests(unittest.TestCase):
    create = base.NotificationTests.create
    update = base.NotificationTests.update
    jobs = base.NotificationTests.jobs
    events = base.NotificationTests.events
    dispatch = base.NotificationTests.dispatch
    linked = base.NotificationTests.linked
    snapshot = base.NotificationTests.snapshot

    def setUp(self):
        base.NotificationTests.setUp(self)
        for name, value in [("find_inquiry_issues", []), ("create_issue", "TEST-42")]:
            p = patch.object(jira, name, return_value=value)
            mock = p.start()
            setattr(self, name, mock)
            self.addCleanup(p.stop)

    def admin_item(self, rid):
        return next(q for q in self.client.get('/admin/inquiries',headers={'x-test-user':'admin'}).json() if q['id']==rid)

    def review(self, rid, route='direct', note='검토 메모', version=None, user='admin'):
        if version is None:
            version=self.admin_item(rid)['review_version']
        return self.client.post(f'/admin/inquiries/{rid}/review',headers={'x-test-user':user},json={
            'route':route,'internal_note':note,'expected_version':version})

    def jira_create(self, rid, **kwargs):
        data={'expected_version':self.admin_item(rid)['review_version'], 'summary':'검토한 제목',
              'description':'개발팀 전달 내용','internal_note':'고객 비공개 메모',**kwargs}
        with patch.object(jira,'on',return_value=True):
            return self.client.post(f'/admin/inquiries/{rid}/jira/create',headers={'x-test-user':'admin'},json=data)

    def test_submission_never_calls_jira_even_when_configured(self):
        with patch.object(jira,'on',return_value=True):
            rid=self.create()
        self.create_issue.assert_not_called()
        self.find_inquiry_issues.assert_not_called()
        self.assertEqual(self.admin_item(rid)['handling_route'],'undecided')
        self.assertEqual([j.kind for j in self.jobs()],['received'])

    def test_direct_route_completion_sends_three_stages_once(self):
        rid=self.create()
        self.assertEqual(self.review(rid).status_code,200)
        self.assertEqual(self.update(rid,'완료','처리 결과입니다.').status_code,200)
        self.assertEqual(self.update(rid,'완료','처리 결과입니다.').status_code,200)
        for _ in range(3):self.dispatch()
        self.assertEqual(self.transport.call_count,3)
        self.assertTrue(all(j.status=='sent' for j in self.jobs()))
        self.create_issue.assert_not_called()

    def test_direct_completion_requires_public_answer(self):
        rid=self.create()
        self.assertEqual(self.update(rid,'완료','').status_code,422)
        self.assertEqual(self.admin_item(rid)['status'],'접수')

    def test_private_review_does_not_leak_or_queue_extra_mail(self):
        rid=self.create()
        self.review(rid,note='비공개 내부 검토 내용')
        self.review(rid,note='비공개 수정 메모')
        customer=self.client.get('/me/inquiries',headers={'x-test-user':'owner'}).json()[0]
        self.assertNotIn('internal_note',customer)
        self.assertNotIn('비공개',str(customer))
        self.assertTrue(all('비공개' not in j.body for j in self.jobs()))
        self.assertEqual([j.kind for j in self.jobs()],['received','started'])

    def test_jira_only_created_by_explicit_admin_request_and_idempotent(self):
        rid=self.create()
        result=self.jira_create(rid)
        self.assertEqual(result.status_code,200)
        item=result.json()['inquiry']
        self.assertEqual(item['jira_key'],'TEST-42')
        self.assertEqual(item['handling_route'],'jira')
        self.assertEqual(item['status'],'처리 중')
        self.assertEqual(self.jira_create(rid,expected_version=0).status_code,200)
        self.create_issue.assert_called_once()
        args=self.create_issue.call_args.args
        self.assertIn(rid,args[1])
        self.assertIn(jira.inquiry_label(rid),args[3])
        self.assertNotIn('고객 비공개 메모',str(args))

    def test_review_and_jira_endpoints_admin_only(self):
        rid=self.create()
        for suffix,body in [('review',{'route':'direct','expected_version':0}),
                            ('jira/create',{'expected_version':0}),('jira/link',{'expected_version':0,'issue_key':'TEST-1'})]:
            path=f'/admin/inquiries/{rid}/{suffix}'
            self.assertEqual(self.client.post(path,json=body).status_code,401)
            self.assertEqual(self.client.post(path,json=body,headers={'x-test-user':'owner'}).status_code,403)
        self.create_issue.assert_not_called()

    def test_stale_review_prevents_overwriting_another_admin(self):
        rid=self.create()
        self.review(rid)
        self.assertEqual(self.review(rid,version=0).status_code,409)
        self.assertEqual(self.jira_create(rid,expected_version=0).status_code,409)
        self.create_issue.assert_not_called()

    def test_definitive_jira_failure_allows_direct_resolution(self):
        rid=self.create()
        self.create_issue.side_effect=jira.JiraError('rejected')
        result=self.jira_create(rid)
        self.assertEqual(result.json()['inquiry']['jira_creation_state'],'failed')
        self.assertEqual(self.review(rid).status_code,200)
        self.assertEqual(self.update(rid,'완료','사이트에서 해결했어요.').status_code,200)

    def test_unknown_create_blocks_direct_and_automatic_retry(self):
        rid=self.create()
        self.create_issue.side_effect=jira.JiraError('lost response',uncertain=True)
        result=self.jira_create(rid)
        self.assertEqual(result.json()['inquiry']['jira_creation_state'],'unknown')
        self.assertEqual(self.review(rid).status_code,409)
        self.assertEqual(self.update(rid,'완료','답변').status_code,409)
        self.assertEqual(self.jira_create(rid).status_code,409)
        self.assertEqual(self.create_issue.call_count,1)

    def test_unknown_result_recovers_existing_issue_without_creation(self):
        rid=self.create()
        with Session(self.engine) as session:
            r=session.get(InquiryReview,rid);r.creation_state='unknown';session.add(r);session.commit()
        self.find_inquiry_issues.return_value=['TEST-77']
        result=self.jira_create(rid)
        self.assertEqual(result.json()['inquiry']['jira_key'],'TEST-77')
        self.create_issue.assert_not_called()

    def test_unknown_requires_confirmation_before_new_creation(self):
        rid=self.create()
        with Session(self.engine) as session:
            r=session.get(InquiryReview,rid);r.creation_state='unknown';session.add(r);session.commit()
        self.assertEqual(self.jira_create(rid,confirmed_not_created=True).json()['inquiry']['jira_key'],'TEST-42')

    def test_search_failure_does_not_create(self):
        rid=self.create();self.find_inquiry_issues.side_effect=OSError()
        self.assertEqual(self.jira_create(rid).status_code,502)
        self.create_issue.assert_not_called()

    def test_creation_in_progress_blocks_other_choices(self):
        rid=self.create()
        with Session(self.engine) as session:
            r=session.get(InquiryReview,rid);r.creation_state='creating';r.creation_started_at=now();session.add(r);session.commit()
        self.assertEqual(self.jira_create(rid).status_code,409)
        self.assertEqual(self.review(rid).status_code,409)
        self.create_issue.assert_not_called()

    def test_completed_direct_must_reopen_before_jira(self):
        rid=self.create();self.update(rid,'완료','안내 완료')
        self.assertEqual(self.jira_create(rid).status_code,409)
        self.update(rid,'처리 중')
        self.assertEqual(self.jira_create(rid).status_code,200)

    def test_reviewed_jira_backlog_does_not_revert_to_unreviewed(self):
        rid=self.create();self.jira_create(rid)
        with Session(self.engine) as session:
            q=flow.locked_inquiry(session,rid);flow.sync_jira(q,self.snapshot('new'),session);session.commit()
            self.assertEqual(q.status,'처리 중')

    def test_direct_inquiry_ignores_jira_snapshot(self):
        rid=self.create();self.review(rid)
        with Session(self.engine) as session:
            q=flow.locked_inquiry(session,rid)
            self.assertFalse(flow.sync_jira(q,self.snapshot('done'),session))
            self.assertEqual(q.status,'처리 중')

    def test_jira_complete_waits_for_answer_then_sends_once(self):
        rid=self.create();self.jira_create(rid)
        with Session(self.engine) as session:
            q=flow.locked_inquiry(session,rid);flow.sync_jira(q,self.snapshot('done'),session);session.commit()
        self.assertEqual(self.jobs()[-1].status,'waiting_answer')
        self.update(rid,answer='고객에게 안내하는 해결 결과')
        for _ in range(3):self.dispatch()
        self.assertEqual([j.kind for j in self.jobs()],['received','started','completed'])
        self.assertEqual(self.transport.call_count,3)

    def test_legacy_queries_do_not_create_mail_or_change_inquiry(self):
        with Session(self.engine) as session:
            for id,status,key in [('old-direct','완료',''),('old-jira','처리 중','TEST-10'),('old-pending','접수','')]:
                session.add(Inquiry(id=id,type='기타',title='기존 문의',body='내용',status=status,jira_key=key))
            session.commit()
        rows=self.client.get('/admin/inquiries',headers={'x-test-user':'admin'}).json()
        routes={q['id']:q['handling_route'] for q in rows}
        self.assertEqual(routes,{'old-direct':'direct','old-jira':'jira','old-pending':'undecided'})
        self.assertEqual(self.jobs(),[])
        self.assertEqual(self.events(),[])

    def test_missing_duplicate_lock_never_creates_issue(self):
        rid=self.create()
        from backend.core import redis_store
        for failure in [False, OSError()]:
            with patch.object(redis_store,'lock_acquire',side_effect=failure if isinstance(failure,Exception) else None,
                              return_value=False):
                self.assertIn(self.jira_create(rid).status_code,(409,503))
        self.create_issue.assert_not_called()

    def test_existing_issue_link_requires_matching_receipt_and_prevents_wrong_link(self):
        rid=self.create()
        path=f'/admin/inquiries/{rid}/jira/link'
        headers={'x-test-user':'admin'}
        body={'expected_version':0,'issue_key':'TEST-55'}
        with patch.object(jira,'on',return_value=True),patch.object(jira,'inquiry_issue_matches',return_value=False):
            self.assertEqual(self.client.post(path,headers=headers,json=body).status_code,409)
        with patch.object(jira,'on',return_value=True),patch.object(jira,'inquiry_issue_matches',return_value=True):
            result=self.client.post(path,headers=headers,json=body)
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['inquiry']['jira_key'],'TEST-55')
        self.create_issue.assert_not_called()

    def test_external_success_then_local_failure_recovers_without_duplicate(self):
        from backend import inquiry_review
        rid=self.create()
        original=inquiry_review.record
        def record(review,q,admin,action,note,session):
            if action=='Jira 등록 완료':raise RuntimeError('simulated_db_failure')
            return original(review,q,admin,action,note,session)
        with patch.object(inquiry_review,'record',side_effect=record):
            with self.assertRaises(RuntimeError):self.jira_create(rid)
        with Session(self.engine) as session:
            r=session.get(InquiryReview,rid)
            self.assertEqual(r.creation_state,'creating')
            self.assertEqual(session.get(Inquiry,rid).jira_key,'')
            r.creation_started_at=now()-timedelta(seconds=121)
            session.add(r);session.commit()
        self.find_inquiry_issues.return_value=['TEST-42']
        self.assertEqual(self.jira_create(rid).json()['inquiry']['jira_key'],'TEST-42')
        self.create_issue.assert_called_once()


class JiraBoundaryTests(unittest.TestCase):
    def test_definitive_and_uncertain_http_failures_are_distinguished(self):
        settings=SimpleNamespace(jira_base_url='https://jira.example.invalid',jira_email='test',jira_api_token='test')
        with patch.object(jira,'get_settings',return_value=settings):
            for status,uncertain in [(400,False),(401,False),(429,False),(500,True),(408,True)]:
                with patch.object(jira.httpx,'request',return_value=Mock(status_code=status,text='PRIVATE RESPONSE')):
                    with self.assertRaises(jira.JiraError) as error:jira._req('POST','/rest/api/3/issue')
                    self.assertEqual(error.exception.uncertain,uncertain)
                    self.assertNotIn('PRIVATE RESPONSE',str(error.exception))
            with patch.object(jira.httpx,'request',side_effect=httpx.ReadTimeout('PRIVATE URL')):
                with self.assertRaises(jira.JiraError) as error:jira._req('POST','/rest/api/3/issue')
                self.assertTrue(error.exception.uncertain)
                self.assertNotIn('PRIVATE URL',str(error.exception))

    def test_recovery_search_is_scoped_to_project_and_receipt(self):
        with patch.object(jira,'get_settings',return_value=SimpleNamespace(jira_project_key='TEST')), \
             patch.object(jira,'_req',return_value={'issues':[{'key':'TEST-1'}]}) as request:
            self.assertEqual(jira.find_inquiry_issues('R-1113-ABCD'),['TEST-1'])
            query=request.call_args.kwargs['params']['jql']
            self.assertIn('project = "TEST"',query)
            self.assertIn('sea-inquiry-r-1113-abcd',query)
