"""DB 전환 설정. 자격증명 원문을 출력하거나 예제 파일에 복사하지 않는다."""
import argparse
import json
from pathlib import Path
import shutil

from dotenv import dotenv_values, set_key
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inspect-jira", "update"])
    args = parser.parse_args()
    local = dotenv_values(ROOT / ".env")
    source = dotenv_values(r"C:\KBR\team\sea_info_v2\.env")
    if args.action == "inspect-jira":
        import httpx
        required = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT_KEY")
        if not all(local.get(key) for key in required):
            print(json.dumps({"configured": False}))
            return
        response = httpx.get(local["JIRA_BASE_URL"].rstrip("/") + "/rest/api/3/project/" + local["JIRA_PROJECT_KEY"],
                            auth=(local["JIRA_EMAIL"], local["JIRA_API_TOKEN"]), timeout=15, follow_redirects=False)
        result = {"http_status": response.status_code}
        if response.status_code == 200:
            names = [row["name"] for row in response.json().get("issueTypes", [])]
            result.update(available_types=names, configured_bug=local.get("JIRA_ISSUE_TYPE_BUG", "Bug"),
                          configured_task=local.get("JIRA_ISSUE_TYPE_DEFAULT", "Task"))
        print(json.dumps(result, ensure_ascii=True))
        return
    run = ROOT / "data/backups/supabase_migration_20261112_final"
    if not (run / "result.json").exists():
        raise RuntimeError("successful_migration_required")
    if local.get("SUPABASE_URL", "").rstrip("/") != source.get("SUPABASE_URL", "").rstrip("/"):
        raise RuntimeError("auth_project_mismatch")
    url = make_url(source["DATABASE_URL"])
    if url.get_backend_name() != "postgresql" or not url.password:
        raise RuntimeError("invalid_remote_url")
    for name in (".env", ".env.example"):
        backup = run / (name + ".before")
        if backup.exists():
            raise RuntimeError("env_backup_already_exists")
        shutil.copyfile(ROOT / name, backup)
    updates = {"DATABASE_URL": source["DATABASE_URL"], "DATABASE_SCHEMA": "sea_info", "DB_SEED_DEMO": "false",
               "BACKEND_URL": "http://localhost:8000", "JIRA_ISSUE_TYPE_DEFAULT": "작업", "JIRA_ISSUE_TYPE_BUG": "작업"}
    for key, value in updates.items():
        set_key(ROOT / ".env", key, value)
    example = dict(updates)
    example["DATABASE_URL"] = "postgresql+psycopg2://DB_USER:DB_PASSWORD@DB_HOST:6543/postgres?sslmode=require"
    for key, value in example.items():
        set_key(ROOT / ".env.example", key, value)
    final = dotenv_values(ROOT / ".env")
    assert make_url(final["DATABASE_URL"]).get_backend_name() == "postgresql"
    print(json.dumps({"env_updated": True, "backend": "postgresql", "schema": "sea_info", "demo_seed": False,
                      "jira_task_and_bug_mapping": "project_task_type", "other_settings_preserved": True}))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error_type": type(error).__name__}))
        raise SystemExit(1)
