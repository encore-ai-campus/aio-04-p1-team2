"""회원·비회원·관리자가 함께 사용하는 고객 공개 처리 이력."""
import streamlit as st


def render(item: dict):
    history = item.get("history") or []
    if not history:
        return
    with st.expander("처리 이력 · 최근 50개"):
        for event in history:
            st.markdown(f"**{event['status']}** · {event['created_at']}")
            st.write(event.get("message", ""))
