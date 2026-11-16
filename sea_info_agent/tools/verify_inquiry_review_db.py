"""실제 PostgreSQL에서 새 흐름을 검증하고 시험 문의·알림을 전부 롤백한다.

SMTP·Jira는 대역으로 차단한다. 외부 메일·이슈는 생성하지 않는다.
"""
import json
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from backend import inquiry_workflow as flow
from backend.db import engine
from backend.models import Inquiry, User
from backend.inquiry_workflow_models import InquiryNotification
from backend.routers import inquiries


def run():
    ids=[]
    checks={}
    create=Mock(return_value='VERIFY-1')
    with engine.connect() as connection:
        transaction=connection.begin()
        try:
            with Session(connection,join_transaction_mode='create_savepoint') as s:
                admin=s.exec(select(User).where(User.role=='admin')).first()
                if not admin:raise RuntimeError('admin_missing')
                actor=User(id=admin.id,nickname=admin.nickname,email=admin.email,role='admin')
            def session():
                with Session(connection,join_transaction_mode='create_savepoint') as s:yield s
            app=FastAPI();app.include_router(inquiries.router)
            app.dependency_overrides[inquiries.get_session]=session
            app.dependency_overrides[inquiries.optional_user]=lambda:None
            app.dependency_overrides[inquiries.require_admin]=lambda:actor
            with patch.object(inquiries,'_notify_ops',return_value=''), \
                 patch.object(inquiries.redis_store,'gap_ok',return_value=True), \
                 patch.object(inquiries.redis_store,'lock_acquire',return_value='verify'), \
                 patch.object(inquiries.redis_store,'lock_release'), \
                 patch.object(inquiries.jira,'on',return_value=True), \
                 patch.object(inquiries.jira,'create_issue',create), \
                 patch.object(inquiries.jira,'find_inquiry_issues',return_value=[]),TestClient(app) as client:
                def request(method,path,body=None):
                    response=client.request(method,path,json=body)
                    if response.status_code!=200:raise RuntimeError('http_'+str(response.status_code))
                    return response.json()
                def new():
                    result=request('POST','/inquiries',{'type':'오류 제보','title':'[롤백검증] 관리자 검토 흐름',
                        'body':'외부 전송 없이 트랜잭션 종료 후 제거되는 시험 문의','email':'verification@example.invalid'})
                    ids.append(result['id'])
                    assert not result['jira_key'] and result['jira_status']=='not_registered'
                    return result['id']
                direct=new();create.assert_not_called();checks['submission_no_jira']=True
                result=request('POST',f'/admin/inquiries/{direct}/review',{'route':'direct','internal_note':'비공개 검토', 'expected_version':0})
                version=result['inquiry']['review_version']
                result=request('PATCH',f'/admin/inquiries/{direct}',{'status':'완료','answer':'직접 처리한 결과 안내', 'expected_version':version})
                assert result['inquiry']['status']=='완료'
                checks['direct_resolution']=True
                linked=new()
                result=request('POST',f'/admin/inquiries/{linked}/jira/create',{'expected_version':0,'summary':'검토 제목','description':'전달 내용'})
                assert result['inquiry']['jira_key']=='VERIFY-1'
                create.assert_called_once();checks['explicit_jira_creation']=True
                with Session(connection,join_transaction_mode='create_savepoint') as s:
                    q=flow.locked_inquiry(s,linked)
                    flow.sync_jira(q,{'category':'done','name':'완료','updated':''},s)
                    s.commit()
                    jobs=s.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id==linked)).all()
                    assert any(j.status=='waiting_answer' for j in jobs)
                result=request('PATCH',f'/admin/inquiries/{linked}',{'answer':'개발 처리 결과 안내'})
                assert result['inquiry']['status']=='완료'
                checks['jira_sync_and_answer_required']=True
                with Session(connection,join_transaction_mode='create_savepoint') as s:
                    for rid in ids:
                        jobs=s.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id==rid).order_by(InquiryNotification.version)).all()
                        assert [j.kind for j in jobs]==['received','started','completed']
                        assert all(j.status=='pending' and j.attempts==0 for j in jobs)
                        assert all('비공개' not in j.body for j in jobs)
                checks['mail_queue_and_private_note']=True
        finally:
            transaction.rollback()
    with Session(engine) as s:
        assert not s.exec(select(Inquiry).where(Inquiry.id.in_(ids))).all()
        assert not s.exec(select(InquiryNotification).where(InquiryNotification.inquiry_id.in_(ids))).all()
    checks['all_test_rows_rolled_back']=True
    result={'checks':checks,'real_smtp_sent':False,'real_jira_created':False}
    out=Path('data/backups/inquiry_review_20261113');out.mkdir(parents=True,exist_ok=True)
    (out/'db_verification.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':run()
