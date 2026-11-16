"""기존 테이블은 변경하지 않고 문의 워크플로 테이블만 추가한다."""
import argparse
import json
from pathlib import Path

from sqlalchemy import inspect, select, text
from sqlmodel import SQLModel

from backend import models, community_models, inquiry_workflow_models
from backend.config import get_settings
from backend.core.timefmt import now
from backend.db import engine
from backend.inquiry_workflow_models import InquiryEvent, InquiryNotification, InquirySyncState

TABLES = [InquiryEvent.__table__, InquiryNotification.__table__, InquirySyncState.__table__]


def snapshot(connection, schema, names):
    result = {}
    for name in sorted(names):
        # 모든 행을 서버에서 정렬·해시한다. 개인정보·레코드 내용은 출력하지 않는다.
        sql = f'''SELECT count(*), md5(coalesce(string_agg(row_hash, '' ORDER BY row_hash), ''))
                  FROM (SELECT md5(row_to_json(t)::text) row_hash FROM "{schema}"."{name}" t) q'''
        count, digest = connection.execute(text(sql)).one()
        result[name] = {"count": count, "digest": digest}
    return result


def run(apply=False):
    if engine.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL required")
    schema = get_settings().database_schema
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(741112031)"))
        existing = set(inspect(connection).get_table_names(schema=schema))
        new_names = {table.name for table in TABLES}
        old_names = existing - new_names
        before = snapshot(connection, schema, old_names)
        result = {"schema": schema, "applied": apply, "missing_before": sorted(new_names-existing),
                  "existing_before": before, "timestamp": now().isoformat()}
        if apply:
            SQLModel.metadata.create_all(connection, tables=TABLES, checkfirst=True)
            roles = set(connection.execute(text("SELECT rolname FROM pg_roles")).scalars())
            for table in TABLES:
                quoted = f'"{schema}"."{table.name}"'
                connection.execute(text(f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY"))
                connection.execute(text(f"REVOKE ALL ON TABLE {quoted} FROM PUBLIC"))
                for role in ("anon", "authenticated"):
                    if role in roles:
                        connection.execute(text(f'REVOKE ALL ON TABLE {quoted} FROM "{role}"'))
            # 기존 문의는 기준 상태만 등록하고 과거 이력·메일은 생성하지 않는다.
            connection.execute(text(f'''INSERT INTO "{schema}".inquiry_sync_state
                (inquiry_id, version, baseline_at, poll_after, poll_claim)
                SELECT id, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '' FROM "{schema}".inquiry
                ON CONFLICT (inquiry_id) DO NOTHING'''))
        result["existing_after"] = snapshot(connection, schema, old_names)
        result["existing_unchanged"] = result["existing_before"] == result["existing_after"]
        if not result["existing_unchanged"]:
            raise RuntimeError("Existing data changed during verification; migration rolled back")
        final = set(inspect(connection).get_table_names(schema=schema))
        result["missing_after"] = sorted(new_names-final)
        if apply:
            result["new_tables"] = snapshot(connection, schema, new_names)
            result["rls"] = dict(connection.execute(text('''SELECT relname, relrowsecurity FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=:schema AND relname IN
                ('inquiry_event','inquiry_notification','inquiry_sync_state')'''), {"schema": schema}).all())
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.apply)
        target = Path("data/backups/inquiry_workflow_20261112")
        target.mkdir(parents=True, exist_ok=True)
        (target / ("migration.json" if args.apply else "plan.json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: result[key] for key in ("applied", "missing_before", "missing_after", "existing_unchanged")}, ensure_ascii=False))
    except Exception as exc:
        print("Migration failed: " + type(exc).__name__)
        raise SystemExit(1)
