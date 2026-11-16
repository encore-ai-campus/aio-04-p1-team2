"""실제 Edge에서 지도 선택·지도 아래 상세·Streamlit 왕복을 검증한다."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from unittest.mock import Mock, patch

from fastapi import FastAPI
from playwright.sync_api import sync_playwright, expect
import uvicorn

from backend.core.timefmt import now
from backend.core.tide_stations import stations_by_id
from backend.routers import tide

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/tide_browser_verification"


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    OUT.mkdir(exist_ok=True)
    cache, calls, errors = {}, [], []
    fail = set()
    def upstream(url, params, **kwargs):
        code = params["obsCode"]
        kind = "forecast" if url == tide.API_URL else "observation"
        calls.append((kind, code))
        if code in fail:
            raise OSError("provider unavailable")
        s = stations_by_id()[code]
        base = {"obsvtrNm": s["name"], "lat": s["latitude"], "lot": s["longitude"]}
        if kind == "forecast":
            rows = [{**base, "predcDt": now().date().isoformat() + " " + hour, "extrSe": k, "predcTdlvVl": v}
                    for hour, k, v in [("02:00", "2", 20), ("08:00", "1", 90), ("14:00", "4", 10), ("20:00", "3", 100)]]
        else:
            rows = [{**base, "obsrvnDt": now().isoformat(timespec="minutes"), "bscTdlvHgt": 30, "wtem": 19}]
        return Mock(status_code=200, json=lambda: {"response": {"header": {"resultCode": "00"},
                    "body": {"items": {"item": rows}, "totalCount": len(rows)}}})
    patches = [patch.object(tide.httpx, "get", side_effect=upstream),
               patch.object(tide.redis_store, "cache_get", side_effect=lambda key: cache.get(key)),
               patch.object(tide.redis_store, "cache_set", side_effect=lambda key, value, ttl: cache.update({key: value}))]
    for p in patches:
        p.start()
    api_port, ui_port = port(), port()
    app = FastAPI()
    app.include_router(tide.router)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning"))
    worker = threading.Thread(target=server.run, daemon=True)
    worker.start()
    env = {**os.environ, "BACKEND_URL": f"http://127.0.0.1:{api_port}", "PYTHONIOENCODING": "utf-8"}
    logfile = (OUT / "streamlit.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "tools/fixtures/tide_home_preview.py",
            "--server.port", str(ui_port), "--server.headless", "true", "--browser.gatherUsageStats", "false",
            "--server.fileWatcherType", "none", "--server.address", "127.0.0.1"],
            cwd=ROOT, env=env, stdout=logfile, stderr=logfile)
    try:
        for _ in range(60):
            try:
                with socket.create_connection(("127.0.0.1", ui_port), timeout=.3):
                    break
            except OSError:
                time.sleep(.3)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1100}, device_scale_factor=1)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"http://127.0.0.1:{ui_port}")
            page.evaluate("window.tideEvents=[];window.addEventListener('message',e=>{if(e.data?.type==='streamlit:setComponentValue'&&e.data.value?.request_id)window.tideEvents.push(e.data.value)})")
            page.get_by_text("공식 예보지점 166곳", exact=False).wait_for(timeout=45000)
            frame = page.frame_locator('iframe[title="components.tide_map.sea_tide_map"]')
            frame.locator('#status').filter(has_text="166곳").wait_for(timeout=30000)
            assert not calls, "홈 진입 시 상세 외부 호출 발생"
            map_frame=next(f for f in page.frames if 'sea_tide_map' in f.url)
            def map_zoom():
                return map_frame.evaluate("Math.max(...Array.from(document.querySelectorAll('img.leaflet-tile')).map(i=>Number(new URL(i.src).pathname.split('/')[1])))")
            def wait_zoom(expected):
                map_frame.wait_for_function("expected=>Math.max(...Array.from(document.querySelectorAll('img.leaflet-tile')).map(i=>Number(new URL(i.src).pathname.split('/')[1])))===expected",arg=expected,timeout=10000)
            initial_zoom=map_zoom()
            frame.locator('#map').hover()
            initial_scroll=page.locator('[data-testid="stMain"]').evaluate('(el)=>el.scrollTop')
            page.mouse.wheel(0,-120)
            wait_zoom(initial_zoom+1)
            page.wait_for_timeout(350)
            assert page.locator('[data-testid="stMain"]').evaluate('(el)=>el.scrollTop')==initial_scroll, '지도 휠이 페이지까지 스크롤함'
            page.mouse.wheel(0,120)
            wait_zoom(initial_zoom)
            page.wait_for_timeout(350)
            assert not calls,'지도 휠 확대·축소로 물때 API를 호출함'
            # 지도 밖에서는 페이지가 이동하고 지도 확대 수준은 유지한다.
            page.locator('input[placeholder="생물 이름 입력"]').hover()
            page.mouse.wheel(0,240)
            page.wait_for_timeout(400)
            assert page.locator('[data-testid="stMain"]').evaluate('(el)=>el.scrollTop')>initial_scroll,'지도 밖 페이지 스크롤 실패'
            assert map_zoom()==initial_zoom,'지도 밖 휠이 지도를 확대함'
            page.locator('[data-testid="stMain"]').evaluate('(el)=>el.scrollTo(0,0)')
            search=page.locator('input[placeholder="생물 이름 입력"]')
            search.fill('불가사리')
            assert page.locator('.st-key-home_species_search_card [data-testid="InputInstructions"]:visible').count()==0
            page.get_by_text('Enter 또는 검색 버튼으로 검색 · 최대 100자',exact=True).wait_for()
            page.screenshot(path=str(OUT/'desktop.png'),full_page=True)
            f = next(f for f in page.frames if 'sea_tide_map' in f.url)
            clusters=frame.locator('.marker-cluster').count()
            assert clusters > 0, '밀집 지점 묶음 없음'
            common = {s['name']: code for code, s in stations_by_id().items() if s['observation_id']}
            selected_code = None
            for _ in range(9):
                visible=frame.locator('.leaflet-marker-icon[title]')
                for i in range(visible.count()):
                    target=visible.nth(i)
                    name=target.get_attribute('title')
                    if name in common and target.is_visible():
                        selected_code=common[name]
                        selected_name=name
                        target.hover()
                        before=len(calls)
                        page.wait_for_timeout(250)
                        assert len(calls)==before, '툴팁에서 API 호출 발생'
                        target.click()
                        break
                if selected_code:
                    break
                cluster=frame.locator('.marker-cluster').first
                if cluster.count():cluster.click()
                else:frame.locator('.leaflet-control-zoom-out').click()
            else:
                raise AssertionError('공통 관측소 개별 깃발에 도달하지 못함')
            detail=page.locator('.st-key-tide_selected_detail')
            detail.get_by_text('현재 관측값',exact=True).wait_for(timeout=15000)
            assert calls.count(('forecast',selected_code))==1,(calls,selected_code,detail.inner_text())
            assert calls.count(('observation',selected_code))==1
            expect(detail.locator('tbody tr')).to_have_count(4)
            assert frame.locator('.leaflet-popup-content').count()==0
            frame.locator(f'.leaflet-marker-icon[title="{selected_name}"]').click()
            page.wait_for_timeout(500)
            assert calls.count(('forecast',selected_code))==1,'재선택 캐시 실패'
            # 지연된 다른 지점의 응답은 현재 지도 선택을 덮어쓰면 안 된다.
            f.evaluate("window.dispatchEvent(new MessageEvent('message',{source:window.parent,data:{type:'streamlit:render',args:{response:{request_id:'old-response',station_id:'SO_0553',error:'과거 지점 오류'}}}}))")
            assert selected_name in frame.locator('#status').inner_text()
            detail.get_by_role('button',name='다시 불러오기',exact=True).click()
            page.wait_for_timeout(700)
            assert calls.count(('forecast',selected_code))==2
            fail.add(selected_code)
            detail.get_by_role('button',name='다시 불러오기',exact=True).click()
            detail.get_by_role('button',name='다시 시도',exact=True).wait_for(timeout=15000)
            assert selected_name in detail.inner_text()
            fail.clear()
            detail.get_by_role('button',name='다시 시도',exact=True).click()
            detail.get_by_text('현재 관측값',exact=True).wait_for(timeout=15000)
            # 상세를 닫지 않고 다른 깃발을 선택해 정보가 교체되는지 확인한다.
            for candidate in ['이어도','신안가거초','옹진소청초']:
                target=frame.locator(f'.leaflet-marker-icon[title="{candidate}"]')
                if candidate!=selected_name and target.count() and target.is_visible():
                    target.click()
                    detail.get_by_text(f'{candidate} · {common[candidate]}',exact=True).wait_for(timeout=15000)
                    break
            else:
                raise AssertionError('상세를 유지한 채 다른 깃발로 전환하지 못함')
            assert frame.locator('.leaflet-popup-content').count()==0
            detail.screenshot(path=str(OUT/'detail.png'))
            page.set_viewport_size({'width':390,'height':1000})
            page.wait_for_timeout(600)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), '모바일 가로 넘침'
            page.screenshot(path=str(OUT/'mobile.png'), full_page=True)
            mobile=browser.new_page(viewport={'width':390,'height':1000},is_mobile=True,has_touch=True,device_scale_factor=1)
            mobile.goto(f'http://127.0.0.1:{ui_port}')
            mobile_frame=mobile.frame_locator('iframe[title="components.tide_map.sea_tide_map"]')
            mobile_frame.locator('#status').filter(has_text='166곳').wait_for(timeout=30000)
            mobile_frame.locator('.leaflet-marker-icon[title="신안가거초"]').tap()
            mobile_detail=mobile.locator('.st-key-tide_selected_detail')
            mobile_detail.get_by_text('현재 관측값',exact=True).wait_for(timeout=15000)
            expect(mobile_detail.locator('tbody tr')).to_have_count(4)
            assert mobile_frame.locator('.leaflet-popup-content').count()==0
            mobile_detail.screenshot(path=str(OUT/'mobile-detail.png'))
            mobile.close()
            page.goto(f'http://127.0.0.1:{ui_port}/?screen=species')
            page.locator('.st-key-species_name_search input').fill('불가사리')
            assert page.locator('.st-key-species_name_search [data-testid="InputInstructions"]:visible').count()==0
            page.locator('.st-key-species_name_search').screenshot(path=str(OUT/'search.png'))
            assert not errors, errors
            browser.close()
        (OUT/'result.json').write_text(json.dumps({'passed':True,'cluster_groups':clusters,'upstream_calls':calls,'browser_errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
        print('Browser verification passed: wheel zoom in/out, page scroll outside map, no API calls on zoom, flags, detail below map, cache, retry, mobile tap.')
    finally:
        process.terminate()
        process.wait(timeout=15)
        logfile.close()
        server.should_exit=True
        worker.join(timeout=10)
        for p in reversed(patches):
            p.stop()


if __name__=='__main__':
    main()
