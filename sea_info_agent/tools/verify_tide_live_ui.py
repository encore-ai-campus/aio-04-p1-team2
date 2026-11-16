"""현재 실행 중인 홈에서 실제 물때 선택을 확인한다. 로그인·쓰기 동작 없음."""
from pathlib import Path
import json
from playwright.sync_api import sync_playwright, expect

OUT = Path(__file__).resolve().parents[1] / "data/tide_live_ui"
OUT.mkdir(exist_ok=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless=True)
        page = browser.new_page(viewport={"width":1400,"height":1100})
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8501')
        frame=page.frame_locator('iframe[title="components.tide_map.sea_tide_map"]')
        frame.locator('#status').filter(has_text='166곳').wait_for(timeout=45000)
        marker=frame.locator('.leaflet-marker-icon[title="신안가거초"]')
        marker.click()
        page.wait_for_timeout(7000)
        detail=page.locator('.st-key-tide_selected_detail')
        expect(detail).to_have_count(1)
        expect(detail).to_contain_text('신안가거초 · IE_0061')
        assert detail.bounding_box()['y'] >= frame.locator('#map').bounding_box()['y'] + frame.locator('#map').bounding_box()['height']
        frame.locator('.leaflet-marker-icon[title="이어도"]').click()
        expect(detail).to_contain_text('이어도 · IE_0060',timeout=20000)
        # 하루 극치 개수는 지점·날짜별로 달라지므로 4회로 고정하지 않는다.
        expect(detail.locator('tbody tr').first).to_be_visible()
        detail.screenshot(path=str(OUT/'detail.png'))
        result={'detail_count':detail.count(),'map_status':frame.locator('#status').inner_text(),
                'alerts':page.locator('[data-testid="stAlert"]').all_text_contents(),'browser_errors':errors,
                'detail_text':detail.all_text_contents(),'switched_without_close':True}
        page.screenshot(path=str(OUT/'home.png'),full_page=True)
        (OUT/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True))
        browser.close()


if __name__=='__main__':
    main()
