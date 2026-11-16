"""차유진 담당: A-02 게시판 행 드래그·상태 편집 컴포넌트."""
import streamlit.components.v2 as v2


# 2026.11.13 - yujin -"패키지 추가 없이 게시판 행 드래그, 키보드 이동, 상태 선택을 지원해요."
_editor = v2.component(
    "sea_info_board_order",
    html='<div class="board-table"><table><thead></thead><tbody></tbody></table></div><p class="hint" aria-live="polite"></p>',
    css="""
        :host {font-family:var(--st-font, sans-serif); color:#12344D; font-size:14px;}
        /* 2026.11.13 - yujin -"표 자체의 너비를 기준으로 작은 화면의 게시판 행을 재배치해요." */
        .board-table {overflow-x:auto; border:1px solid #DCE6E9; border-radius:12px;container-type:inline-size;}
        table {width:100%; min-width:960px; border-collapse:collapse; text-align:left;}
        th {background:#EAF6F7; font-weight:600; white-space:nowrap;}
        th,td {padding:10px 12px; border-bottom:1px solid #DCE6E9;}
        tbody tr:last-child td {border-bottom:0;}
        tbody tr {background:var(--st-background-color,white);}
        tbody tr:hover {background:#F5FAFB;}
        tr.dragging {opacity:.55;}
        .handle {cursor:grab; border:0; border-radius:6px; background:transparent;
            color:#4A6274; font-size:20px; width:36px; height:36px;}
        .handle:active {cursor:grabbing;}
        .handle {touch-action:none;}
        .compact-meta {display:none;}
        .handle:focus-visible,select:focus-visible {outline:2px solid #0B7285; outline-offset:2px;}
        select {border:1px solid #DCE6E9; border-radius:8px; padding:7px; color:#12344D;
            background:var(--st-background-color,white); font:inherit; min-width:108px;}
        .hint {font-size:12px; color:#6B8291; margin:8px 0 0;}
        /* 2026.11.13 - yujin -"태블릿은 주요 열을 유지하고 나머지 정보를 이름 아래로 옮겨 가로 스크롤을 줄여요." */
        @container (max-width:900px) {
            table {min-width:0;table-layout:fixed;}
            th,td {padding:10px 8px;overflow-wrap:anywhere;}
            th:nth-child(1),td:nth-child(1) {width:52px;}
            th:nth-child(7),td:nth-child(7) {width:114px;}
            th:nth-child(8),td:nth-child(8) {width:60px;}
            th:nth-child(2),td:nth-child(2),th:nth-child(4),td:nth-child(4),
            th:nth-child(5),td:nth-child(5),th:nth-child(6),td:nth-child(6),
            th:nth-child(9),td:nth-child(9),th:nth-child(10),td:nth-child(10) {display:none;}
            .compact-meta {display:block;font-size:12px;line-height:1.6;color:#4A6274;margin-top:4px;font-weight:400;}
            .handle {width:44px;height:44px;}
            select {min-height:44px;font-size:16px;min-width:0;width:100%;}
        }
        /* 2026.11.13 - yujin -"모바일은 이름·유형 정보와 상태·글 수를 두 줄의 둥근 행으로 정리해요." */
        @container (max-width:540px) {
            table,tbody {display:block;width:100%;}
            thead {display:none;}
            tbody {padding:8px;box-sizing:border-box;}
            tbody tr {display:grid;grid-template-columns:44px minmax(0,1fr) 100px;
                grid-template-rows:auto auto;gap:4px 8px;border:1px solid #DCE6E9;border-radius:12px;padding:10px;margin-bottom:10px;align-items:center;}
            tbody tr:last-child {margin-bottom:0;}
            td {display:block;border:0 !important;padding:0;width:auto !important;min-width:0;}
            td:nth-child(1) {grid-column:1;grid-row:1/3;}
            td:nth-child(3) {grid-column:2;grid-row:1/3;}
            td:nth-child(7) {grid-column:3;grid-row:1;}
            td:nth-child(8) {grid-column:3;grid-row:2;text-align:right;font-size:12px;color:#4A6274;}
            td:nth-child(8)::before {content:'글 ';}
            td:nth-child(8)::after {content:'개';}
        }
    """,
    js="""
    export default function({parentElement, data, setStateValue}) {
        const table = parentElement.querySelector('table');
        const head = table.querySelector('thead');
        const body = table.querySelector('tbody');
        const hint = parentElement.querySelector('.hint');
        head.replaceChildren(); body.replaceChildren();
        const heading = document.createElement('tr');
        for (const name of ['이동','순서','이름','고유 주소','유형','목록 형태','상태','글 수','추가 항목','구분']) {
            const cell = document.createElement('th'); cell.textContent = name; heading.append(cell);
        }
        head.append(heading);
        hint.textContent = '왼쪽 손잡이를 드래그하거나 손잡이에 초점을 두고 ↑·↓ 키로 이동해요.';
        let dragging = null, original = [], dropped = false;
        const rows = () => Array.from(body.children);
        const renumber = () => rows().forEach((row,i) => {
            row.querySelector('.position').textContent = i+1;
            row.querySelector('.compact-position').textContent = '순서 '+(i+1)+' · ';
        });
        const commit = () => {
            renumber();
            const current = rows();
            setStateValue('model', {ids:current.map(row=>row.dataset.id),
                status:Object.fromEntries(current.map(row=>[row.dataset.id,row.querySelector('select').value]))});
        };
        for (const item of data.rows) {
            const row = document.createElement('tr'); row.dataset.id = item.id;
            const cell = (text, cls) => {
                const td = document.createElement('td'); td.textContent = String(text ?? '');
                if (cls) td.className = cls; row.append(td); return td;
            };
            const handle = document.createElement('button');
            handle.type='button'; handle.className='handle'; handle.draggable=true;
            handle.textContent='⠿'; handle.setAttribute('aria-label', item.name+' 순서 이동');
            handle.title='드래그 또는 위·아래 방향키로 순서 이동';
            cell('').append(handle); cell('', 'position');
            for (const name of ['name','id','type','layout']) cell(item[name]);
            // 2026.11.13 - yujin -"축약 화면에서도 고유키·유형·목록 형태·추가 항목·기본 여부를 빠짐없이 표시해요."
            const metadata=document.createElement('span');metadata.className='compact-meta';
            const position=document.createElement('span');position.className='compact-position';
            metadata.append(position,document.createTextNode(item.id+' · '+item.type+' · '+item.layout+' · 추가 항목 '+item.fields+'개 · '+item.system));
            row.children[2].append(metadata);
            const select = document.createElement('select');
            select.setAttribute('aria-label',item.name+' 상태');
            for (const state of data.statuses) {
                const option=document.createElement('option'); option.value=state; option.textContent=state;
                select.append(option);
            }
            select.value=item.status; select.onchange=commit; cell('').append(select);
            for (const name of ['posts','fields','system']) cell(item[name]);
            handle.ondragstart = event => {
                dragging=row; original=rows(); dropped=false; row.classList.add('dragging');
                event.dataTransfer.effectAllowed='move'; event.dataTransfer.setData('text/plain',item.id);
                event.dataTransfer.setDragImage(row,20,20);
            };
            handle.ondragend = () => {
                row.classList.remove('dragging');
                if (!dropped) {for (const old of original) body.append(old); renumber();}
                dragging=null;
            };
            handle.onkeydown = event => {
                if (!['ArrowUp','ArrowDown'].includes(event.key)) return;
                event.preventDefault();
                if (event.key==='ArrowUp' && row.previousElementSibling) body.insertBefore(row,row.previousElementSibling);
                else if (event.key==='ArrowDown' && row.nextElementSibling) body.insertBefore(row.nextElementSibling,row);
                else return;
                commit();
            };
            // 2026.11.13 - yujin -"터치 손잡이 드래그도 순서 변경을 지원하고 취소하면 원래 순서로 돌려요."
            let touchPointer=null, touchOriginal=[], touchMoved=false;
            handle.onpointerdown = event => {
                if (event.pointerType==='mouse' || !event.isPrimary) return;
                event.preventDefault();touchPointer=event.pointerId;touchOriginal=rows();touchMoved=false;
                handle.setPointerCapture(event.pointerId);row.classList.add('dragging');
            };
            handle.onpointermove = event => {
                if (event.pointerId!==touchPointer) return;
                const target=rows().find(other=>{const box=other.getBoundingClientRect();return event.clientY>=box.top&&event.clientY<=box.bottom;});
                if (!target||target===row) return;
                const box=target.getBoundingClientRect();
                body.insertBefore(row,event.clientY<box.top+box.height/2?target:target.nextSibling);
                handle.setPointerCapture(event.pointerId);
                touchMoved=true;renumber();
            };
            const finishTouch = (event,cancelled) => {
                if (event.pointerId!==touchPointer) return;
                touchPointer=null;row.classList.remove('dragging');
                if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
                if(cancelled){for(const old of touchOriginal)body.append(old);renumber();}
                else if(touchMoved)commit();
            };
            handle.onpointerup=event=>finishTouch(event,false);
            handle.onpointercancel=event=>finishTouch(event,true);
            body.append(row);
        }
        renumber();
        const over = event => {
            if (!dragging) return;
            event.preventDefault(); event.dataTransfer.dropEffect='move';
            const target=event.target.closest('tbody tr');
            if (!target || target===dragging || target.parentElement!==body) return;
            const box=target.getBoundingClientRect();
            body.insertBefore(dragging,event.clientY<box.top+box.height/2 ? target : target.nextSibling);
            renumber();
        };
        const drop = event => {if(dragging) {event.preventDefault(); dropped=true; commit();}};
        body.addEventListener('dragover',over); body.addEventListener('drop',drop);
        return () => {body.removeEventListener('dragover',over); body.removeEventListener('drop',drop);};
    }
    """,
)


def board_order(rows: list[dict], statuses: list[str], *, key: str, on_change) -> None:
    """행 데이터는 data로 전달하고 JS에서는 textContent로 표시해 HTML 삽입을 막아요."""
    initial = {"ids": [r["id"] for r in rows], "status": {r["id"]: r["status"] for r in rows}}
    _editor(data={"rows": rows, "statuses": statuses}, default={"model": initial}, key=key,
            on_model_change=on_change)
