"""브라우저 회귀용 홈 상단. 제품 함수와 스타일을 그대로 실행한다."""
import ast
from pathlib import Path
import streamlit as st

st.set_page_config(layout="wide", page_title="홈 검색·물때 검증")
root = Path(__file__).resolve().parents[2]
source = ast.parse((root / "views/home.py").read_text(encoding="utf-8"))
definitions = [n for n in source.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
exec(compile(ast.Module(body=definitions, type_ignores=[]), str(root / "views/home.py"), "exec"))
S = st.session_state
user = None
shot = None
db.remaining = lambda user: (3, 3)
ui.load_css()
if st.query_params.get("screen") == "species":
    source = ast.parse((root / "views/species_search.py").read_text(encoding="utf-8"))
    definitions = [n for n in source.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(root / "views/species_search.py"), "exec"))
    query = ""
    ui.page_header("생물 이름 검색", "이름으로 찾고, 국립해양생물자원관과 WoRMS의 정보를 함께 확인해요.")
    search_box()
    st.stop()
ui.page_header("반가워요!", "사진 한 장으로 바다 생물의 이름을 알아봐요.")
left, right = st.columns([3, 2], gap="medium")
with left:
    hero()
with right:
    species_search_box()
    st.space("small")
    tide_card()
