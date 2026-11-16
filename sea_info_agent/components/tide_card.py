"""전체 공식 예보지점 지도. 상세 자료는 명시적인 선택·재조회 때만 읽는다."""
from datetime import datetime, timedelta, timezone
import time

import streamlit as st

from components.tide_map import tide_map
from services import auth, db
from services.api import ApiError

KST = timezone(timedelta(hours=9))


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def _load(station_id: str, request_id: str, refresh: bool, fail: bool) -> dict:
    response = {"station_id": station_id, "request_id": request_id}
    try:
        if fail:
            raise ApiError(503, "선택 지점의 예보를 불러오지 못했어요. 다시 시도해 주세요.")
        data = db.station_tide(station_id, refresh=refresh)
        if data.get("station_id") != station_id or data.get("target_date") != _today():
            raise ValueError("자료 대상 불일치")
        response["data"] = data
    except ApiError as exc:
        response["error"] = exc.message
    except Exception:
        response["error"] = "선택 지점의 오늘 물때를 확인하지 못했어요. 다시 시도해 주세요."
    return response


def _list_detail(response: dict, names: dict) -> None:
    """키보드·지도 장애 상황에서도 사용할 수 있는 상세 화면."""
    code = response["station_id"]
    st.markdown(f"**{names[code]} · {code}**")
    if response.get("error"):
        st.warning(response["error"])
        return
    data = response["data"]
    st.caption(f"{data['target_date']} · 한국 시간 · 고·저조 예보")
    if data["target_date"] != _today():
        st.warning("날짜가 바뀌었어요. 오늘 예보를 다시 불러와 주세요.")
    st.table([{"구분": "만조" if e["kind"] == "high" else "간조", "시각": e["time"],
               "예측 조위": f"{e['level_cm']:g} cm" if e.get("level_cm") is not None else "미제공"}
              for e in data.get("events", [])])
    st.markdown("**현재 관측값**")
    if data.get("observation_status") in ("ok", "stale"):
        level, temp = data.get("observation_level_cm"), data.get("observation_water_temp_c")
        st.write(f"조위 {f'{level:g} cm' if level is not None else '미제공'} · "
                 f"수온 {f'{temp:g} °C' if temp is not None else '미제공'}")
        st.caption(f"관측소: {data['observation_station_name']} ({data['observation_station_id']}) · "
                   f"관측 시각: {data['observation_time']}")
        st.caption(f"관측 출처: {data['observation_source']} · 수집 시각: {data.get('observation_collected_at') or '미확인'}")
        if data["observation_status"] == "stale":
            st.warning(data.get("observation_message"))
    else:
        st.caption("관측 조회 실패" if data.get("observation_status") == "error" else "실측 미제공")
        st.caption(data.get("observation_message") or "연결된 실측 자료가 없어요.")
    st.caption(f"예보 출처: {data['source']} · 수집 시각: {data['updated']}")
    st.caption("예보는 실제 관측값이 아니며 현장 안전을 보장하지 않아요.")


def tide_card(fail: bool = False) -> None:
    state = st.session_state
    owner = (auth.current_user() or {}).get("id") or "guest"
    if state.get("tide_map_owner") != owner:
        for key in ("tide_map_response", "tide_map_handled"):
            state.pop(key, None)
        state["tide_map_owner"] = owner
    with st.container(border=True, gap="small", key="home_tide_card"):
        st.markdown('<h2 class="sia-h2">오늘의 물때</h2>', unsafe_allow_html=True)
        st.caption("깃발을 누르면 지도 아래에 오늘의 물때가 나와요.")
        try:
            catalog = db.tide_stations()
            stations = catalog["stations"]
            names = {s["id"]: s["name"] for s in stations}
            if not stations:
                raise ValueError("empty catalog")
        except Exception:
            st.warning("공식 예보지점 목록을 불러오지 못했어요.")
            if st.button("지점 목록 다시 불러오기", key="tide_catalog_retry"):
                db.clear_cache()
                st.rerun()
            return
        event = tide_map(stations, state.get("tide_map_response"))
        if isinstance(event, dict) and event.get("request_id") != state.get("tide_map_handled"):
            request_id, code = event.get("request_id"), event.get("station_id")
            if isinstance(request_id, str) and code in names and event.get("action") in ("select", "refresh", "close"):
                state["tide_map_handled"] = request_id
                if event["action"] == "close":
                    state.pop("tide_map_response", None)
                else:
                    with st.spinner(f"{names[code]} 물때를 확인하고 있어요…"):
                        state["tide_map_response"] = _load(code, request_id, event["action"] == "refresh", fail)
                st.rerun()
        response = state.get("tide_map_response")
        if isinstance(response, dict) and response.get("station_id") in names:
            with st.container(border=True, gap="small", key="tide_selected_detail"):
                _list_detail(response, names)
                with st.container(horizontal=True, gap="small"):
                    if st.button("다시 시도" if response.get("error") else "다시 불러오기", key="tide_detail_refresh"):
                        state["tide_map_response"] = _load(response["station_id"], f"list-{time.time_ns()}", True, fail)
                        st.rerun()
                    if st.button("상세 닫기", key="tide_detail_close"):
                        state.pop("tide_map_response", None)
                        st.rerun()
        with st.expander("지점 이름으로 찾기 · 지도가 안 보일 때"):
            with st.form("tide_station_list", border=False):
                code = st.selectbox("공식 예보지점", sorted(names, key=names.get), index=None,
                                    placeholder="지점 이름을 입력하거나 선택해 주세요", format_func=names.get,
                                    key="tide_list_pick")
                submitted = st.form_submit_button("오늘 물때 보기")
            if submitted:
                if code:
                    with st.spinner(f"{names[code]} 물때를 확인하고 있어요…"):
                        state["tide_map_response"] = _load(code, f"list-{time.time_ns()}", False, fail)
                    st.rerun()
                else:
                    st.info("지점을 먼저 선택해 주세요.")
        st.caption(f"공식 예보지점 {len(stations)}곳 · 축소하면 가까운 지점을 묶어 표시해요.")
        st.caption("출처: 국립해양조사원 · 예보와 실측은 별도 자료예요.")
