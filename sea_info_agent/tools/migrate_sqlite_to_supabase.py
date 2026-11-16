"""SQLite → 기존 Supabase 앱 스키마 병합. 비밀값·레코드 본문을 출력하지 않는다.

plan은 DB를 변경하지 않으며 SQLite의 온라인 백업과 비교 결과를 보관한다.
apply는 검토한 계획·원본 스냅샷을 사용하고 단일 PostgreSQL 트랜잭션으로 삽입한다.
"""
import argparse
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from dotenv import dotenv_values
from sqlalchemy import Boolean, DateTime, JSON, MetaData, create_engine, inspect, select, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))


def remote_engine(env_path):
    values = dotenv_values(env_path)
    url = make_url(values["DATABASE_URL"])
    if url.get_backend_name() != "postgresql":
        raise ValueError("remote_is_not_postgresql")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg2")
    schema = values.get("DATABASE_SCHEMA") or "sea_info"
    if not schema.isidentifier():
        raise ValueError("invalid_schema")
    return create_engine(url, connect_args={"connect_timeout": 8}), schema


def converted(row, table):
    result = {}
    for column in table.columns:
        if column.name not in row:
            continue
        value = row[column.name]
        if value is not None:
            if isinstance(column.type, JSON) and isinstance(value, str):
                value = json.loads(value)
            elif isinstance(column.type, Boolean):
                value = bool(value)
            elif isinstance(column.type, DateTime):
                if isinstance(value, str):
                    value = datetime.fromisoformat(value)
                if value.tzinfo is None:
                    value = value.replace(tzinfo=KST)
                value = value.astimezone(timezone.utc)
        result[column.name] = value
    return result


def canonical(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else value.replace(tzinfo=KST).astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {key: canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [canonical(item) for item in value]
    return value


def fingerprint(row):
    return hashlib.sha256(json.dumps(canonical(dict(row)), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def read_source(path):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return {name: [dict(row) for row in connection.execute(f'SELECT * FROM "{name}"')]
                for name, in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        connection.close()


def compare(connection, schema, source, metadata=None):
    if metadata is None:
        metadata = MetaData(schema=schema)
        metadata.reflect(connection)
    plan, rows_by_table, comment_map = {}, {}, {}
    for table in metadata.sorted_tables:
        name = table.name
        rows = [converted(row, table) for row in source.get(name, [])]
        pk = [column.name for column in table.primary_key]
        if not pk:
            raise ValueError("table_without_primary_key")
        target = [dict(row) for row in connection.execute(select(table)).mappings()]
        by_key = {tuple(row[key] for key in pk): row for row in target}
        natural = defaultdict(deque)
        if name == "comment":
            local_keys = {row["id"] for row in rows}
            for row in sorted(target, key=lambda item: str(item["id"])):
                if row["id"] not in local_keys:
                    natural[(row["post_id"], row["user_id"], row["body"], row["deleted"])].append(row)
        natural_matches = 0
        inserts, conflicts, changed_columns = [], [], Counter()
        for row in rows:
            if name == "comment_reply":
                row["comment_id"] = comment_map.get(row["comment_id"], row["comment_id"])
                row["parent_id"] = comment_map.get(row["parent_id"], row["parent_id"])
            key = tuple(row[column] for column in pk)
            existing = by_key.get(key)
            if name == "comment" and existing is None:
                matches = natural[(row["post_id"], row["user_id"], row["body"], row["deleted"])]
                if matches:
                    existing = matches.popleft()
                    comment_map[row["id"]] = existing["id"]
                    natural_matches += 1
            if existing is None:
                inserts.append(row)
            else:
                changes = [column for column in row if canonical(row[column]) != canonical(existing.get(column))]
                if changes:
                    conflicts.append({"key_hash": hashlib.sha256(repr(key).encode()).hexdigest()[:12], "columns": changes})
                    changed_columns.update(changes)
        # 고유 제약의 충돌도 적용 전에 탐지한다.
        unique_groups = [constraint["column_names"] for constraint in inspect(connection).get_unique_constraints(name, schema=schema)]
        unique_conflicts = 0
        for columns in unique_groups:
            values = {tuple(canonical(row[col]) for col in columns) for row in target if all(row[col] is not None for col in columns)}
            for row in inserts:
                value = tuple(canonical(row[col]) for col in columns)
                if all(item is not None for item in value):
                    if value in values:
                        unique_conflicts += 1
                    values.add(value)
        plan[name] = {"source": len(rows), "remote": len(target), "insert": len(inserts),
                      "existing": len(rows) - len(inserts), "different_existing": len(conflicts),
                      "natural_matches": natural_matches,
                      "changed_columns": dict(changed_columns), "unique_conflicts": unique_conflicts,
                      "remote_digest": fingerprint({str(index): fingerprint(row) for index, row in enumerate(sorted(target, key=lambda row: repr(tuple(row[col] for col in pk))))})}
        rows_by_table[name] = (table, inserts, conflicts)
    missing = set(source) - set(plan)
    if missing:
        plan["_missing_tables"] = sorted(missing)
    metadata.info["comment_map"] = comment_map
    return plan, rows_by_table


def apply_merge(engine, schema, source, run, expected):
    from sqlalchemy import Table
    with engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(text("SET LOCAL statement_timeout = '30s'"))
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": schema + ":sqlite_merge"})
        names = inspect(connection).get_table_names(schema=schema)
        for name in sorted(names):
            if not name.isidentifier():
                raise ValueError("invalid_table_name")
            connection.execute(text(f'LOCK TABLE "{schema}"."{name}" IN SHARE ROW EXCLUSIVE MODE'))
        before, rows = compare(connection, schema, source)
        if before != expected:
            raise ValueError("plan_changed_no_writes_applied")
        if any(row.get("unique_conflicts", 0) for name, row in before.items() if not name.startswith("_")):
            raise ValueError("unique_conflicts_require_resolution")
        metadata = next(iter(rows.values()))[0].metadata
        backup = {name: [canonical(dict(row)) for row in connection.execute(select(table)).mappings()]
                  for name, (table, _, _) in rows.items()}
        (run / "remote_before.json").write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
        missing = set(before.get("_missing_tables", []))
        if missing - {"comment_reply", "report_detail"}:
            raise ValueError("unsupported_schema_difference")
        if "comment_reply" in missing:
            connection.execute(text(f'''CREATE TABLE "{schema}".comment_reply (
                comment_id VARCHAR PRIMARY KEY REFERENCES "{schema}".comment(id) ON DELETE CASCADE,
                parent_id VARCHAR NOT NULL REFERENCES "{schema}".comment(id) ON DELETE CASCADE)'''))
            connection.execute(text(f'CREATE INDEX ix_comment_reply_parent_id ON "{schema}".comment_reply(parent_id)'))
        if "report_detail" in missing:
            connection.execute(text(f'''CREATE TABLE "{schema}".report_detail (
                report_id INTEGER PRIMARY KEY REFERENCES "{schema}".report(id) ON DELETE CASCADE,
                detail VARCHAR NOT NULL)'''))
        for name in sorted(missing):
            connection.execute(text(f'ALTER TABLE "{schema}"."{name}" ENABLE ROW LEVEL SECURITY'))
            connection.execute(text(f'REVOKE ALL ON "{schema}"."{name}" FROM PUBLIC, anon, authenticated'))
            Table(name, metadata, autoload_with=connection, schema=schema)
        reviewed, rows = compare(connection, schema, source, metadata)
        inserted = {}
        for name, (table, inserts, conflicts) in rows.items():
            if inserts:
                connection.execute(table.insert(), inserts)
            inserted[name] = len(inserts)
        # 새 댓글·답글을 반영한 게시글 표시용 개수만 실제 레코드 수에 맞춘다.
        connection.execute(text(f'''UPDATE "{schema}".post AS p SET comments = (
            SELECT count(*) FROM "{schema}".comment AS c WHERE c.post_id=p.id AND c.deleted=false)
            WHERE comments <> (SELECT count(*) FROM "{schema}".comment AS c WHERE c.post_id=p.id AND c.deleted=false)'''))
        final, _ = compare(connection, schema, source, metadata)
        if any(row["insert"] or row["unique_conflicts"] for row in final.values()):
            raise ValueError("post_merge_contains_unmigrated_records")
        for name, row in final.items():
            if row["remote"] != reviewed[name]["remote"] + inserted[name]:
                raise ValueError("unexpected_final_count")
        # 수동 PK 삽입 후에도 다음 자동 ID가 충돌하지 않게 시퀀스를 앞으로만 이동한다.
        sequence_adjustments = []
        for table in metadata.sorted_tables:
            if len(table.primary_key.columns) != 1:
                continue
            column = list(table.primary_key.columns)[0]
            qualified = f'"{schema}"."{table.name}"'
            sequence = connection.execute(text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                                           {"table_name": qualified, "column_name": column.name}).scalar()
            if not sequence:
                continue
            maximum = connection.execute(text(f'SELECT max("{column.name}") FROM {qualified}')).scalar()
            if maximum is None:
                continue
            seq_schema, seq_name = sequence.replace('"', '').split('.')
            if not seq_schema.isidentifier() or not seq_name.isidentifier():
                raise ValueError("invalid_sequence")
            last, called = connection.execute(text(f'SELECT last_value,is_called FROM "{seq_schema}"."{seq_name}"')).one()
            if maximum > last or (maximum == last and not called):
                connection.execute(text("SELECT setval(CAST(:sequence AS regclass), :value, true)"), {"sequence": sequence, "value": maximum})
                sequence_adjustments.append(table.name)
        result = {"schema": schema, "inserted": inserted, "before": before, "after": final,
                  "added_tables": sorted(missing), "comment_id_mappings": len(metadata.info["comment_map"]),
                  "sequence_adjustments": sequence_adjustments, "idempotent_recheck": "pass",
                  "conflict_policy": "preserve_existing_remote_values_except_derived_comment_counts"}
    (run / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"inserted": inserted, "added_tables": sorted(missing), "idempotent_recheck": "pass"}, ensure_ascii=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["plan", "apply"])
    parser.add_argument("--remote-env", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--expected-plan")
    args = parser.parse_args()
    run = Path(args.run_dir).resolve()
    if not run.is_relative_to(ROOT / "data"):
        raise ValueError("backup_directory_must_be_in_project_data")
    run.mkdir(parents=True, exist_ok=True)
    snapshot = run / "local.sqlite3"
    if args.action == "plan" or not snapshot.exists():
        if snapshot.exists():
            raise ValueError("use_new_backup_directory")
        with sqlite3.connect((ROOT / "data/sea_info.db").as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(snapshot) as backup:
                original.backup(backup)
    source = read_source(snapshot)
    engine, schema = remote_engine(args.remote_env)
    try:
        if args.action == "apply":
            if not args.expected_plan:
                raise ValueError("expected_plan_required")
            expected = json.loads(Path(args.expected_plan).read_text(encoding="utf-8"))
            apply_merge(engine, schema, source, run, expected)
            return
        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            plan, rows = compare(connection, schema, source)
        if args.action == "plan":
            (run / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(plan, ensure_ascii=True, indent=2))
            return
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        import traceback
        print(json.dumps({"error_type": type(error).__name__, "frames": [frame.name for frame in traceback.extract_tb(error.__traceback__)[-3:]]}))
        sys.exit(1)
