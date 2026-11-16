"""수족관 저장 진단. 개인 메모·위치·메일·인증값은 출력하지 않는다."""
import json
from pathlib import Path

import httpx
from sqlalchemy import text

from tools.migrate_sqlite_to_supabase import remote_engine

ROOT = Path(__file__).resolve().parents[1]


def main():
    for port in (8000, 8002, 8005):
        try:
            response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=3)
            data = response.json()
            print(json.dumps({"api_port": port, "status": response.status_code,
                              "database_backend": data.get("database_backend"), "database_schema": data.get("database_schema")}))
        except Exception:
            pass
    engine, schema = remote_engine(ROOT / ".env")
    try:
        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            total = connection.execute(text(f'SELECT count(*) FROM "{schema}".observation')).scalar()
            rows = connection.execute(text(f'''SELECT o.id,o.user_id,o.created_at,o.photo_path,s.name FROM "{schema}".observation o
                JOIN "{schema}".species s ON s.id=o.species_id ORDER BY o.created_at DESC LIMIT 6''')).mappings().all()
            print(json.dumps({"supabase_observation_count": total, "recent": [
                {"species_name": row["name"], "created_at": row["created_at"].isoformat(),
                 "has_photo": bool(row["photo_path"]), "file_exists": bool(row["photo_path"] and (ROOT / "data/uploads" / row["photo_path"]).is_file()),
                 "seed_member": row["user_id"] == "u_me"} for row in rows]}, ensure_ascii=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
