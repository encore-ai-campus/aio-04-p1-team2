"""실제 API·Supabase에 연결한 완료 화면 검사. 시험 관찰만 생성 후 삭제한다."""
import json
from pathlib import Path
import uuid
from unittest.mock import patch

import httpx
from sqlalchemy import text
from streamlit.testing.v1 import AppTest

from tools.migrate_sqlite_to_supabase import remote_engine

ROOT = Path(__file__).resolve().parents[1]


def main():
    marker = "identify-completion-test-" + uuid.uuid4().hex
    result = {"checks": {}}
    engine, schema = remote_engine(ROOT / ".env")
    token = None
    observation_id = None
    client = httpx.Client(base_url="http://localhost:8000", timeout=45)
    try:
        login = client.post("/auth/dev-login", json={"kind": "member"})
        login.raise_for_status()
        session = login.json()
        token = session["token"]
        headers = {"Authorization": f"Bearer {token}"}
        example = client.post("/identify", data={"example": "소라게"}, headers=headers)
        example.raise_for_status()
        at = AppTest.from_file(str(ROOT / "views/identify.py"), default_timeout=60)
        at.session_state["user"] = session["user"]
        at.session_state["token"] = token
        at.session_state["identify_step"] = "save"
        at.session_state["identify_result"] = example.json()["result"]
        with patch("components.location_map.choose_place", return_value=""):
            at.run()
            assert not at.exception
            at.text_area[0].input(marker)
            at.button(key="do_save").click().run()
            assert not at.exception
            assert at.session_state["identify_step"] == "done"
            assert any(button.label == "수족관 보기" for button in at.button)
            assert not any(button.key == "do_save" for button in at.button)
            assert at.success
            result["checks"]["actual_save_to_completion_screen"] = "pass"
            with engine.connect() as connection:
                ids = connection.execute(text(f'SELECT id FROM "{schema}".observation WHERE memo=:marker'), {"marker": marker}).scalars().all()
            assert len(ids) == 1
            observation_id = ids[0]
            result["checks"]["supabase_committed_exactly_once"] = "pass"
            at.run()
            assert not at.exception and at.success
            with engine.connect() as connection:
                count = connection.execute(text(f'SELECT count(*) FROM "{schema}".observation WHERE memo=:marker'), {"marker": marker}).scalar()
            assert count == 1
            result["checks"]["rerun_keeps_completion_without_duplicate"] = "pass"
        result["status"] = "pass"
    except Exception as error:
        result.update(status="failed", error_type=type(error).__name__)
    finally:
        if token:
            if observation_id is None:
                with engine.connect() as connection:
                    observation_id = connection.execute(text(f'SELECT id FROM "{schema}".observation WHERE memo=:marker'), {"marker": marker}).scalar()
            if observation_id:
                removed = client.delete(f"/me/observations/{observation_id}", headers={"Authorization": f"Bearer {token}"})
                result["checks"]["test_record_cleanup"] = "pass" if removed.status_code == 200 else "failed"
            client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
        client.close()
        engine.dispose()
        path = ROOT / "data/backups/identify_completion_verification.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True))
    return 0 if result.get("status") == "pass" and result["checks"].get("test_record_cleanup") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
