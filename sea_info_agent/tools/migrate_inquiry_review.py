"""기존 문의·메일 이력을 보존하며 관리자 검토 전용 테이블을 추가한다."""
import argparse
import json
from pathlib import Path
from sqlalchemy import inspect, text
from sqlmodel import SQLModel
from backend import models, inquiry_workflow_models
from backend.config import get_settings
from backend.core.timefmt import now
from backend.db import engine
from backend.inquiry_workflow_models import InquiryReview
from tools.migrate_inquiry_workflow import snapshot


def run(apply=False):
    if engine.dialect.name != "postgresql":
        raise RuntimeError("PostgreSQL migration only; SQLite startup creates the new table")
    schema = get_settings().database_schema
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            connection.execute(text("SELECT pg_advisory_xact_lock(741113041)"))
            existing = set(inspect(connection).get_table_names(schema=schema))
            old_names = existing - {"inquiry_review"}
            before = snapshot(connection, schema, old_names)
            result = {"applied": apply, "missing_before": "inquiry_review" not in existing,
                      "before": before, "timestamp": now().isoformat()}
            if apply:
                SQLModel.metadata.create_all(connection, tables=[InquiryReview.__table__], checkfirst=True)
                quoted = f'"{schema}"."inquiry_review"'
                connection.execute(text(f"ALTER TABLE {quoted} ENABLE ROW LEVEL SECURITY"))
                connection.execute(text(f"REVOKE ALL ON TABLE {quoted} FROM PUBLIC"))
                roles = set(connection.execute(text("SELECT rolname FROM pg_roles")).scalars())
                for role in ("anon", "authenticated"):
                    if role in roles:
                        connection.execute(text(f'REVOKE ALL ON TABLE {quoted} FROM "{role}"'))
                connection.execute(text(f'''INSERT INTO {quoted}
                    (inquiry_id, route, version, reviewed_by, internal_note, creation_state, jira_summary, jira_description)
                    SELECT id, CASE WHEN jira_key <> '' THEN 'jira' WHEN status = '완료' THEN 'direct' ELSE 'undecided' END,
                    0, '', '', CASE WHEN jira_key <> '' THEN 'created'
                    WHEN jira_status IN ('failed','unknown') THEN 'unknown' ELSE 'unregistered' END, '', ''
                    FROM "{schema}".inquiry ON CONFLICT (inquiry_id) DO NOTHING'''))
            result["after"] = snapshot(connection, schema, old_names)
            result["existing_unchanged"] = before == result["after"]
            if not result["existing_unchanged"]:
                raise RuntimeError("Existing data changed; transaction rolled back")
            if apply:
                result["routes"] = dict(connection.execute(text(f'SELECT route, count(*) FROM "{schema}".inquiry_review GROUP BY route')).all())
                result["rls"] = bool(connection.execute(text('''SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n
                    ON n.oid=c.relnamespace WHERE n.nspname=:schema AND c.relname='inquiry_review' '''), {"schema":schema}).scalar())
    out = Path("data/backups/inquiry_review_20261113")
    out.mkdir(parents=True, exist_ok=True)
    (out / ("migration.json" if apply else "plan.json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return {k:v for k,v in result.items() if k not in ("before","after")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args.apply), ensure_ascii=True))
    except Exception as exc:
        print("Migration failed: " + type(exc).__name__)
        raise SystemExit(1)
