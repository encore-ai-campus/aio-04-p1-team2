"""차유진 담당 화면 전용 반응형 스타일. 공통 테마·사이드바 파일은 변경하지 않아요."""
import streamlit as st


def load_responsive(screen: str) -> None:
    # 2026.11.13 - yujin -"U-08·U-09·U-10·A-02에만 반응형 스타일을 적용해 다른 담당 화면에 영향을 주지 않아요."
    if screen not in ("community", "write", "boards"):
        raise ValueError("지원하지 않는 담당 화면이에요.")
    st.html(f'<span class="yujin-responsive-page yujin-responsive-{screen}" aria-hidden="true"></span>')
    st.html("""<style>
        /* 2026.11.13 - yujin -"태블릿에서는 담당 화면의 넓은 폼을 압축하고 긴 글·버튼이 가로로 넘치지 않게 해요." */
        [data-testid="stMainBlockContainer"]:has(.yujin-responsive-page) {min-width:0;}
        [data-testid="stMainBlockContainer"]:has(.yujin-responsive-page) .sia-title {overflow-wrap:anywhere;}
        .community-post-body {overflow-wrap:anywhere; word-break:normal;}
        .st-key-write_layout {container-type:inline-size;container-name:write-layout;}
        /* 2026.11.13 - yujin -"Streamlit 레이아웃 래퍼 안의 열을 선택해 기기 크기에 따른 재배치를 확실히 적용해요." */
        .st-key-community_observation_grid [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]) {flex-wrap:wrap;}
        .st-key-community_observation_grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {width:calc((100% - 32px)/3);flex:0 0 calc((100% - 32px)/3);min-width:0;}
        @container write-layout (max-width:650px) {
            .st-key-write_layout [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]) {flex-direction:column;gap:20px;}
            .st-key-write_layout [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {width:100%;flex:1 1 100%;min-width:0;}
        }
        @media(max-width:1023px) {
            [data-testid="stMainBlockContainer"]:has(.yujin-responsive-page) {padding-left:24px; padding-right:24px;}
            .st-key-community_tabs_layout [data-testid="stColumn"]:first-child {width:100%; flex:1 1 100%; min-width:0;}
            .st-key-community_tabs_layout [data-testid="stColumn"]:last-child {display:none;}
            .st-key-community_board_tabs button {flex:0 1 auto !important;}
            .st-key-community_detail_layout [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]) {flex-wrap:wrap;}
            .st-key-community_detail_layout [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {width:100%;flex:1 1 100%;min-width:0;}
            .st-key-community_observation_grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {width:calc(50% - 8px);flex:0 0 calc(50% - 8px);min-width:0;}
            .st-key-boards_settings_layout [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]) {flex-wrap:wrap;}
            .st-key-boards_settings_layout [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {flex:1 1 260px;min-width:0;}
            [data-testid="stMainBlockContainer"]:has(.yujin-responsive-boards) [data-testid="stForm"] [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]) {flex-wrap:wrap;}
            [data-testid="stMainBlockContainer"]:has(.yujin-responsive-boards) [data-testid="stForm"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {flex:1 1 220px;min-width:0;}
        }
        /* 2026.11.13 - yujin -"모바일은 본문·공개 설정·관리 폼을 한 열로 배치하고 터치 입력과 답글 들여쓰기를 조정해요." */
        @media(max-width:767px) {
            .st-key-write_layout [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]),
            .st-key-community_search_layout [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"]) {flex-direction:column;gap:16px;}
            .st-key-write_layout [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
            .st-key-community_search_layout [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {width:100%;flex:1 1 100%;min-width:0;}
            [data-testid="stMainBlockContainer"]:has(.yujin-responsive-page) button {min-height:44px;}
            [data-testid="stMainBlockContainer"]:has(.yujin-responsive-page) input,
            [data-testid="stMainBlockContainer"]:has(.yujin-responsive-page) textarea {font-size:16px !important;}
            .st-key-community_board_tabs button p {font-size:14px !important;}
            [class*="st-key-community_reply_row_"] {margin-left:12px !important;}
            .st-key-community_post_actions {flex-wrap:wrap !important;gap:8px !important;}
            .st-key-write_actions {flex-wrap:wrap !important;gap:8px !important;}
        }
        @media(max-width:640px) {
            [data-testid="stMainBlockContainer"]:has(.yujin-responsive-page) {padding-left:16px;padding-right:16px;}
            [data-testid="stMainBlockContainer"]:has(.yujin-responsive-page) .sia-title {font-size:26px;line-height:1.4;}
            [data-testid="stMainBlockContainer"]:has(.yujin-responsive-boards) [data-testid="stForm"] [data-testid="stColumn"] {width:100%;flex:1 1 100% !important;}
            .st-key-community_observation_grid [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {width:100%;flex:1 1 100%;}
            .st-key-community_pagination {gap:2px !important;}
            .st-key-community_pagination button {min-width:32px !important;padding:0 5px !important;}
        }
    </style>""")
