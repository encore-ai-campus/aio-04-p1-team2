"""커밋 후 독립 연결에서 이전 누락·시퀀스·파일·원본 보존을 읽기 전용 확인."""
import json
from pathlib import Path

from sqlalchemy import text

from tools.migrate_sqlite_to_supabase import compare, fingerprint, read_source, remote_engine

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "data/backups/supabase_migration_20261112_final"


def main():
    source = read_source(RUN / "local.sqlite3")
    unchanged = fingerprint(source) == fingerprint(read_source(ROOT / "data/sea_info.db"))
    engine, schema = remote_engine(ROOT / ".env")
    try:
        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            plan, rows = compare(connection, schema, source)
            remaining = sum(row["insert"] for name, row in plan.items() if not name.startswith("_"))
            assert remaining == 0 and not plan.get("_missing_tables"), "missing_source_records"
            assert unchanged, "original_sqlite_changed"
            metadata = next(iter(rows.values()))[0].metadata
            mappings = metadata.info["comment_map"]
            (RUN / "comment_id_map.json").write_text(json.dumps(mappings, ensure_ascii=False, indent=2), encoding="utf-8")
            constraints = connection.execute(text("SELECT convalidated FROM pg_constraint WHERE connamespace=CAST(:schema AS regnamespace) AND contype='f'"), {"schema": schema}).scalars().all()
            assert all(constraints), "unvalidated_foreign_key"
            sequence_checks = []
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
                seq_schema, seq_name = sequence.replace('"', '').split('.')
                assert seq_schema.isidentifier() and seq_name.isidentifier()
                last, called = connection.execute(text(f'SELECT last_value,is_called FROM "{seq_schema}"."{seq_name}"')).one()
                assert maximum is None or last > maximum or (last == maximum and called), "sequence_behind_data"
                sequence_checks.append(table.name)
        files = {row.get("photo_path") for name in ("observation", "post") for row in source.get(name, []) if row.get("photo_path")}
        missing_photos = sum(not (ROOT / "data/uploads" / name).is_file() for name in files)
        result = {"status": "pass", "remaining_source_records_to_insert": remaining, "sqlite_original_unchanged": unchanged,
                  "foreign_keys_validated": len(constraints), "comment_mappings": len(mappings),
                  "sequence_checks": sequence_checks, "referenced_photos": len(files), "missing_photo_files": missing_photos,
                  "remote_counts": {name: row["remote"] for name, row in plan.items() if not name.startswith("_")}}
        (RUN / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1)
