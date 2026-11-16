"""실행 중인 실제 API·관리자 화면을 읽기 전용으로 검증한다."""
from pathlib import Path
import json
import time
import httpx
from sqlmodel import Session, select
from playwright.sync_api import sync_playwright, expect
from backend.core import redis_store
from backend.db import engine
from backend.models import User


def main():
    out=Path('data/backups/inquiry_review_20261113');out.mkdir(parents=True,exist_ok=True)
    token=''
    try:
        with Session(engine) as s:
            admin=s.exec(select(User).where(User.role=='admin')).first()
            if not admin:raise RuntimeError('admin_missing')
            token=redis_store.session_create(admin.id)
        with httpx.Client(base_url='http://127.0.0.1:8005',timeout=30) as client:
            for _ in range(20):
                try:
                    if client.get('/health').status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.5)
            response=client.get('/admin/inquiries',headers={'Authorization':'Bearer '+token})
            response.raise_for_status();rows=response.json()
            assert all('handling_route' in q and 'review_version' in q for q in rows)
            assert all(q['handling_route']=='jira' for q in rows if q['jira_key'])
            paths=client.get('/openapi.json').json()['paths']
            assert '/admin/inquiries/{rid}/jira/create' in paths
            assert '/admin/inquiries/{rid}/review' in paths
            counts={route:sum(q['handling_route']==route for q in rows) for route in ('undecided','direct','jira')}
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=r'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1100})
            context.add_cookies([{'name':'sea_token','value':token,'domain':'127.0.0.1','path':'/'}])
            page=context.new_page()
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:8501/admin-inquiries?shot=detail')
            page.get_by_role('heading',name='문의 관리',exact=True).wait_for(timeout=45000)
            page.get_by_role('combobox',name='처리 방식',exact=True).click()
            page.get_by_role('option',name='검토 대기',exact=True).click()
            page.get_by_role('button',name='직접 처리하기',exact=True).wait_for(timeout=15000)
            expander=page.locator('details').filter(has=page.get_by_text('Jira에 등록하기 · 기존 이슈 확인',exact=True))
            if expander.get_attribute('open') is None:
                expander.locator('summary').click()
            expect(expander.get_by_role('button',name='Jira 이슈 생성',exact=True).or_(expander.get_by_role('button',name='Jira 등록 결과 확인·다시 시도',exact=True))).to_be_disabled()
            expect(page.get_by_role('button',name='저장하기',exact=True)).to_be_disabled()
            page.screenshot(path=str(out/'admin-review.png'),full_page=True)
            assert not errors,errors
            browser.close()
        result={'api_ready':True,'existing_inquiries':len(rows),'routes':counts,'review_ui_ready':True,
                'explicit_confirmation_required':True,'real_jira_created':False,'real_smtp_sent':False}
        (out/'runtime.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result))
    finally:
        if token:redis_store.session_delete(token)


if __name__=='__main__':main()
