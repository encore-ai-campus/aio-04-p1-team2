"""전환된 실제 API의 저장·수정·삭제와 Jira 연동을 검증한다.

검증 관찰은 삭제하고, 실제 Jira와 연결된 검증 문의는 완료 상태로 보존한다.
계정·토큰·키·본문은 결과에 기록하지 않는다.
"""
import json
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
import uuid

import httpx
from dotenv import dotenv_values
from sqlalchemy import text

from tools.migrate_sqlite_to_supabase import remote_engine

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "data/backups/supabase_migration_20261112_final"
RESULT = RUN / "runtime_verification.json"


def check(condition, name):
    if not condition:
        raise AssertionError(name)


def main():
    if RESULT.exists():
        raise RuntimeError("verification_already_recorded")
    result = {"checks": {}, "checked_at": datetime.now(timezone(timedelta(hours=9))).isoformat()}
    engine, schema = remote_engine(ROOT / ".env")
    client = httpx.Client(base_url="http://127.0.0.1:8000", timeout=45)
    tokens = []
    observation_id = None
    step = "health"
    try:
        health = client.get("/health").json()
        check(health.get("ok") and health.get("database_backend") == "postgresql" and health.get("database_schema") == schema, step)
        result["health"] = {key: health.get(key) for key in ("ok", "database_backend", "database_schema", "redis")}
        result["checks"][step] = "pass"
        step = "member_login"
        response = client.post("/auth/dev-login", json={"kind": "member"})
        check(response.status_code == 200, step)
        member = response.json()
        token = member["token"]
        tokens.append(token)
        headers = {"Authorization": f"Bearer {token}"}
        user_id = member["user"]["id"]
        with sqlite3.connect((ROOT / "data/sea_info.db").as_uri() + "?mode=ro", uri=True) as sqlite:
            sqlite_count_before = sqlite.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
        marker = "supabase-storage-check-" + uuid.uuid4().hex
        with engine.connect() as connection:
            before = connection.execute(text(f'SELECT COUNT(*) FROM "{schema}".observation')).scalar()
        step = "api_save_committed_to_supabase"
        example = client.post("/identify", data={"example": "소라게"}, headers=headers)
        check(example.status_code == 200, "example_response")
        response = client.post("/identify/save", headers=headers, json={"result": example.json()["result"],
            "date": datetime.now(timezone(timedelta(hours=9))).date().isoformat(), "memo": marker, "share": False})
        check(response.status_code == 200, step)
        with engine.connect() as connection:
            rows = connection.execute(text(f'SELECT id FROM "{schema}".observation WHERE memo=:marker AND user_id=:uid'),
                                      {"marker": marker, "uid": user_id}).scalars().all()
        check(len(rows) == 1, "independent_database_read_after_commit")
        observation_id = rows[0]
        result["checks"][step] = "pass"
        step = "api_read_matches_saved_record"
        response = client.get("/me/observations", headers=headers)
        check(response.status_code == 200 and any(row["id"] == observation_id for row in response.json()), step)
        result["checks"][step] = "pass"
        step = "api_update_committed_to_supabase"
        response = client.patch(f"/me/observations/{observation_id}", headers=headers, json={"memo": marker + "-updated"})
        check(response.status_code == 200, step)
        with engine.connect() as connection:
            value = connection.execute(text(f'SELECT memo FROM "{schema}".observation WHERE id=:id'), {"id": observation_id}).scalar()
        check(value == marker + "-updated", step)
        result["checks"][step] = "pass"
        step = "verification_record_cleanup"
        check(client.delete(f"/me/observations/{observation_id}", headers=headers).status_code == 200, step)
        observation_id = None
        with engine.connect() as connection:
            after = connection.execute(text(f'SELECT COUNT(*) FROM "{schema}".observation')).scalar()
        check(before == after, step)
        with sqlite3.connect((ROOT / "data/sea_info.db").as_uri() + "?mode=ro", uri=True) as sqlite:
            check(sqlite.execute("SELECT COUNT(*) FROM observation").fetchone()[0] == sqlite_count_before, "sqlite_not_written")
        result["checks"][step] = "pass"
        result["checks"]["sqlite_not_written"] = "pass"
        step = "jira_live_inquiry_creation"
        check(health.get("modes", {}).get("mail") == "dev", "prevent_real_ops_mail")
        response = client.post("/inquiries", headers={"X-Guest-Id": uuid.uuid4().hex}, json={
            "type": "오류 제보", "title": "[검증] Supabase 전환·Jira 연결 확인", "body": "사용자가 요청한 저장소 전환 및 Jira 연결 검증용 문의입니다. 실제 장애가 아닙니다. 완료 상태로 보존합니다.",
            "email": "verification@example.invalid"})
        check(response.status_code == 200, step)
        created = response.json()
        result["inquiry_id"] = created["id"]
        result["jira_key"] = created.get("jira_key", "")
        RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        check(created.get("jira_status") == "not_registered" and not created.get("jira_key"), "inquiry_requires_review")
        with engine.connect() as connection:
            stored = connection.execute(text(f'SELECT jira_key FROM "{schema}".inquiry WHERE id=:id'), {"id": created["id"]}).scalar()
        check(stored == created["jira_key"], "inquiry_and_jira_key_committed")
        result["checks"][step] = "pass"
        step = "jira_duplicate_prevention"
        admin_response = client.post("/auth/dev-login", json={"kind": "admin"})
        check(admin_response.status_code == 200, "admin_login")
        admin_token = admin_response.json()["token"]
        tokens.append(admin_token)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        reviewed = next(x for x in client.get('/admin/inquiries', headers=admin_headers).json() if x['id'] == created['id'])
        linked = client.post(f"/admin/inquiries/{created['id']}/jira/create", headers=admin_headers,
                             json={"expected_version": reviewed['review_version']})
        check(linked.status_code == 200 and linked.json()['inquiry']['jira_key'], 'admin_explicit_jira_creation')
        stored = linked.json()['inquiry']['jira_key']
        result['jira_key'] = stored
        response = client.post(f"/admin/inquiries/{created['id']}/jira/retry", headers=admin_headers)
        check(response.status_code == 409, step)
        result["checks"][step] = "pass"
        step = "jira_done_transition"
        values = dotenv_values(ROOT / ".env")
        base = values["JIRA_BASE_URL"].rstrip("/")
        jira_auth = (values["JIRA_EMAIL"], values["JIRA_API_TOKEN"])
        transitions = httpx.get(f"{base}/rest/api/3/issue/{stored}/transitions", auth=jira_auth, timeout=20).json()
        done = next((item for item in transitions.get("transitions", []) if item.get("to", {}).get("statusCategory", {}).get("key") == "done"), None)
        check(done is not None, "done_transition_available")
        response = httpx.post(f"{base}/rest/api/3/issue/{stored}/transitions", auth=jira_auth, timeout=20,
                              json={"transition": {"id": done["id"]}})
        check(response.status_code == 204, step)
        result["checks"][step] = "pass"
        step = "jira_done_synced_to_supabase"
        response = client.post(f"/admin/inquiries/{created['id']}/jira/sync", headers=admin_headers)
        check(response.status_code == 200 and response.json().get("done") is True, step)
        with engine.connect() as connection:
            status = connection.execute(text(f'SELECT status,jira_status FROM "{schema}".inquiry WHERE id=:id'), {"id": created["id"]}).one()
        check(tuple(status) == ("완료", "done"), step)
        result["checks"][step] = "pass"
        result["jira_url"] = f"{base}/browse/{stored}"
        result["status"] = "pass"
    except Exception as error:
        result.update(status="failed", failed_step=step, error_type=type(error).__name__)
    finally:
        if observation_id and tokens:
            try:
                client.delete(f"/me/observations/{observation_id}", headers={"Authorization": f"Bearer {tokens[0]}"})
            except Exception:
                result["cleanup_required"] = True
        for token in tokens:
            try:
                client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
            except Exception:
                pass
        client.close()
        engine.dispose()
        RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
