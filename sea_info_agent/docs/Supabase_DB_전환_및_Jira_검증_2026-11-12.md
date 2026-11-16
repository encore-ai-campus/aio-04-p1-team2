# Supabase DB 전환과 Jira 연결 검증 보고서

검증일: 2026년 11월 12일

`C:\svn\sea_info`의 앱 데이터 저장소를 로컬 SQLite에서 Supabase PostgreSQL의 `sea_info` 스키마로 전환했다. 기존 원격 데이터를 보존하면서 로컬에만 있던 **8건을 추가**했고, 중복 댓글 36건은 기존 원격 댓글에 일대일 대응했다. 실제 API 저장·조회·수정·삭제와 Jira 문의 생성·완료 동기화 검증은 모두 통과했다.

## 1. 원인과 설정 변경

실행 중인 앱은 `C:\svn\sea_info`였지만 PostgreSQL 접속 정보는 다른 프로젝트인 `C:\KBR\team\sea_info_v2`의 `.env`에만 있었다. 실행 폴더의 `.env`는 SQLite를 가리키고 있었다.

실제 동작을 확인한 PostgreSQL 접속 정보를 실행 프로젝트의 `.env`에 적용했다. 다른 인증·메일·Gemini·Redis 자격증명은 유지했다.

| 설정 | 적용 내용 |
| --- | --- |
| DATABASE_URL | 연결 확인한 Supabase PostgreSQL 주소 적용. 실제 값은 문서에 기록하지 않음 |
| DATABASE_SCHEMA | sea_info |
| DB_SEED_DEMO | false. 원격 예시 데이터 자동 삽입 금지 |
| BACKEND_URL | http://localhost:8000 |
| JIRA_ISSUE_TYPE_DEFAULT | 작업 |
| JIRA_ISSUE_TYPE_BUG | 작업. 현재 프로젝트에 Bug 유형이 없어 기존 오류 제보 제목·내용을 유지한 작업으로 연결 |

`.env.example`에도 PostgreSQL 예제와 동일한 스키마·Jira 유형을 반영했다. DB 접속 예제에는 `DB_USER`, `DB_PASSWORD`, `DB_HOST` 자리표시자만 사용했다. 실제 `.env`의 접속 정보는 예제 파일에 복사하지 않았다.

API와 화면은 해당 프로젝트 경로에서 재시작했다. `/health` 응답에 저장소 종류와 스키마를 추가해 로그인 제공자가 Supabase라는 표시와 앱 DB 저장소를 구분할 수 있게 했다.

- API: `http://127.0.0.1:8000`, 상태 200.
- 화면: `http://127.0.0.1:8501`, 상태 200.
- DB: `database_backend=postgresql`, `database_schema=sea_info`, DB·Redis 정상.

현재 APP_ENV 등 요청과 무관한 운영 모드는 기존 설정을 유지했다. Supabase Auth 사용자를 새로 만들거나 기존 계정 암호를 변경하지 않았다.

## 2. 백업과 이전 정책

앱의 API·화면을 잠시 중지하고 SQLite의 온라인 백업을 사용해 WAL 내용을 포함한 일관된 스냅샷을 만들었다. 원격 데이터와 환경파일도 전환 전 상태를 보관했다. Redis 및 다른 프로젝트는 중지하지 않았다.

백업 위치: `data/backups/supabase_migration_20261112_final/`. 이 폴더는 SVN 제외 대상인 `data` 아래에 있으며 개인정보·환경파일을 포함하므로 외부에 공유하지 않는다.

| 파일 | 내용 |
| --- | --- |
| local.sqlite3 | 최종 로컬 SQLite 스냅샷 |
| remote_before.json | 이전 전 Supabase 앱 테이블의 전체 행 백업 |
| .env.before | 변경 전 실행 환경파일 |
| .env.example.before | 변경 전 예제 환경파일 |
| result.json | 테이블별 삽입·충돌·검증 결과 |
| comment_id_map.json | 로컬 댓글 ID와 기존 원격 댓글 ID 대응 |
| runtime_verification.json | 실제 API·Jira 검증 결과 |
| audit.json | 커밋 후 독립 연결 최종 감사 결과 |

초기 비교 스냅샷은 같은 backups 폴더의 plan·plan2·plan3 디렉터리에 보관했다. 실제 이전에 사용한 백업은 final 디렉터리다.

이전 정책:

1. 같은 기본키의 기존 원격 행은 덮어쓰지 않는다. 원격에만 있는 기록도 유지한다.
2. 댓글은 게시글·작성자·본문·삭제 상태가 동일한 행을 일대일 대응한다. 같은 내용의 댓글이 여러 번 존재하는 경우에도 개수를 보존하며 하나로 무조건 축약하지 않는다.
3. 답글의 부모 ID는 댓글 대응표를 적용한다. 신규 답글·신고 상세 테이블은 RLS를 활성화하고 PUBLIC·anon·authenticated의 직접 권한을 허용하지 않았다.
4. 고유 제약 충돌, 검토 계획 변경, 이전 후 누락 또는 개수 불일치가 발생하면 원격 데이터 트랜잭션을 중단한다.
5. 자동 증가 ID 시퀀스는 기존 값보다 뒤로 돌리지 않고 필요한 경우에만 앞으로 조정한다.
6. 게시글의 댓글 수는 실제 삭제되지 않은 댓글 수에 맞춰 재집계했다.

같은 키지만 내용이 달랐던 주요 항목은 회원의 비밀번호 해시·지역, 문의의 연락처 연결, 생성 시각이었다. 이 값들은 기존 Supabase 값을 유지했다. 로컬 값도 local.sqlite3에 보존되어 있다. 로컬 값을 원격에 일괄 덮어쓰는 작업은 하지 않았다.

## 3. 테이블별 이전 결과

최종 열에는 실제 Jira 검증을 위해 보존한 완료 문의 1건과 조치 기록 1건이 포함된다. 시험 관찰 기록은 삭제했으며, 원격에 원래 더 있던 문의도 유지했다.

| 테이블 | 로컬 | 이전 전 Supabase | 추가 이전 | 이전 직후 Supabase | 실제 검증 후 |
| --- | ---: | ---: | ---: | ---: | ---: |
| adminlog | 3 | 3 | 0 | 3 | 4 |
| board | 5 | 5 | 0 | 5 | 5 |
| inquiry | 5 | 4 | 2 | 6 | 7 |
| news | 5 | 5 | 0 | 5 | 5 |
| newsrun | 2 | 2 | 0 | 2 | 2 |
| newssource | 3 | 3 | 0 | 3 | 3 |
| quizquestion | 6 | 6 | 0 | 6 | 6 |
| scorelog | 0 | 0 | 0 | 0 | 0 |
| setting | 1 | 1 | 0 | 1 | 1 |
| species | 10 | 9 | 1 | 10 | 10 |
| user | 7 | 7 | 0 | 7 | 7 |
| observation | 6 | 4 | 2 | 6 | 6 |
| post | 13 | 12 | 1 | 13 | 13 |
| comment | 38 | 36 | 2 | 38 | 38 |
| like | 0 | 0 | 0 | 0 | 0 |
| report | 4 | 4 | 0 | 4 | 4 |
| comment_reply | 0 | 0 | 0 | 0 | 0 |
| report_detail | 0 | 0 | 0 | 0 | 0 |

신규로 추가한 스키마 요소: `comment_reply`, `report_detail`. 두 테이블의 기존 로컬 행은 0건이었다. 기존 16개 테이블은 삭제하거나 다시 만들지 않았고 현재 앱 스키마는 18개 테이블이다. Supabase 대시보드에서는 `public` 대신 **sea_info 스키마**를 선택해 확인한다.

## 4. 저장 검증 테스트

| ID | 절차 | 기대 결과 | 결과 |
| --- | --- | --- | --- |
| DB01 | 실제 API /health 조회 | PostgreSQL·sea_info·DB·Redis 정상 | 통과 |
| DB02 | 시험 관찰을 API로 담은 뒤 별도 DB 연결로 조회 | 커밋한 새 행이 Supabase에서 1건 조회 | 통과 |
| DB03 | API에서 본인 관찰 목록 조회 | 같은 저장 기록 조회 | 통과 |
| DB04 | 시험 관찰 메모 수정 후 별도 DB 연결로 재조회 | 수정한 값이 원격에 반영 | 통과 |
| DB05 | 시험 관찰 삭제 후 개수 비교 | 시험 행만 삭제, 원래 관찰 개수 복구 | 통과 |
| DB06 | 원본 SQLite 전체 내용 지문 비교 | 전환·시험 전후 로컬 기록 불변 | 통과 |
| DB07 | 이전 커밋 후 새 연결에서 재비교 | 추가 이전할 로컬 기록 0건 | 통과 |
| DB08 | 외래키·시퀀스·사진 확인 | 외래키 검증됨, 자동 ID 충돌 없음, 참조 파일 존재 | 통과 |

외래키 13개 검증 상태 정상, 자동 ID 시퀀스 7개 정상, 참조 사진 1개·누락 0개. 사진 파일은 기존 업로드 디렉터리를 유지했다. 사진을 Supabase Storage로 옮기는 작업은 포함하지 않았다.

원격 전환 이후 실제 API 저장 시험에서 SQLite 내용이 바뀌지 않았음을 별도 비교로 확인했다. 실제 검증은 로컬 모드의 시험용 로그인으로 앱 데이터 API를 호출한 것이며, 새로운 Supabase Auth 가입·메일 인증 검증은 아니다.

## 5. Jira 원인과 실제 검증

Jira 인증과 프로젝트 조회는 HTTP 200으로 정상 응답했다. 기존 설정의 Bug·Task 유형은 해당 프로젝트에 없었고, 실제 유형은 에픽·Subtask·작업·스토리였다. 애플리케이션이 사용하는 일반 제보 및 오류 제보 유형을 현재 제공되는 작업으로 연결했다. Jira 프로젝트의 워크플로·유형 정의 자체는 변경하지 않았다.

| ID | 실제 절차 | 결과 |
| --- | --- | --- |
| JIRA01 | 오류 제보 API → 실제 이슈 생성 → Supabase 문의에 이슈 키 커밋 | 통과 |
| JIRA02 | 동일 문의에 Jira 다시 보내기 호출 | 409로 중복 생성 차단 |
| JIRA03 | 실제 Jira 이슈를 완료 상태로 변경 | 통과 |
| JIRA04 | 관리자 상태 가져오기 → 별도 Supabase 연결 조회 | 문의 status=완료, jira_status=done 확인 |

검증 문의: `R-1112-A6M7`. 시험 이슈: [KAN-7](https://marinebaro12.atlassian.net/browse/KAN-7). 실제 장애가 아닌 검증용임을 제목·본문에 표시했고, 완료 상태의 증빙으로 남겼다. 검증 메일은 실제 발송하지 않았다.

외부 Jira 웹훅의 비밀값 설정 여부: **미설정**. 이번 완료 동기화는 관리자 상태 가져오기 API를 사용했다. 외부 공개 HTTPS 주소로 Jira가 자동 호출하는 웹훅 전달은 검증하지 않았다. 기존 실패 문의를 일괄 재전송하지 않았으며, 필요한 문의는 관리자 화면에서 개별 재시도한다.

## 6. 회귀 검사와 변경 파일

다음 명령의 **35개 검사 모두 통과**:

```powershell
.\.venv\Scripts\python.exe -B -m unittest tools.test_supabase_migration tools.test_community_replies tools.test_community_photos tools.test_community_report_details
```

새 이전 검사 6개는 ID가 다른 같은 댓글 중복 방지, 중복 개수의 일대일 보존, 다른 본문 보존, 답글 부모 ID 대응, 기존 ID 재사용 방지, 같은 계획의 반복 비교를 확인한다. 나머지는 현재 프로젝트의 커뮤니티 회귀 검사다.

이번 변경:

- `.env`: 실제 DB 접속·스키마·시드 비활성화·화면 API 주소·Jira 유형 설정.
- `.env.example`: 비밀값 없는 PostgreSQL 접속 예제 및 관련 설정.
- `backend/routers/core.py`, `backend/schemas/common.py`: /health에 DB 종류·스키마 표시 추가.
- `tools/migrate_sqlite_to_supabase.py`, `tools/test_supabase_migration.py`: 백업·비교·이전·중복 방지와 회귀 검사.
- `tools/configure_supabase_runtime.py`, `tools/verify_supabase_runtime.py`, `tools/audit_supabase_migration.py`: 설정 전환과 실제 실행·최종 감사 도구.
- 이 보고서 및 비밀값을 제외한 실행 결과 요약.

작업 전부터 수정되어 있던 `backend/config.py`, `backend/db.py`, `backend/models.py`, `requirements.txt`, `start.bat`의 사용자 변경은 유지했다. 다른 프로젝트의 `.env`는 읽기만 했고 수정하지 않았다. SVN 커밋은 하지 않았다.

## 7. 운영 확인과 복구

현재 서비스는 8000 API·8501 화면으로 실행 중이다. 향후 start.bat은 기존 기본값 API_PORT=8002를 유지하고 화면에 같은 API 포트를 전달한다. 명시적으로 같은 포트로 시작하려면 CMD에서 `set API_PORT=8000` 후 실행한다. 이미 켜진 서버와 중복 실행하지 않는다.

원격과 로컬의 기록이 다시 갈라지지 않도록 평상시에는 새 `.env`의 PostgreSQL 설정을 유지한다. DB를 되돌릴 필요가 있으면 먼저 앱을 중지하고 .env.before를 복원해 재시작한다. 이 방법은 저장 위치를 되돌리는 것이며 전환 후 Supabase에서 새로 작성한 기록을 SQLite에 자동 복사하지 않는다. 원격 DB 전체 삭제·백업 일괄 덮어쓰기는 복구 과정에서도 자동 실행하지 않는다.

원본 백업에는 자격증명과 개인정보가 포함될 수 있다. `data/backups`를 SVN에 추가하지 않는다. 일반 보고용 결과는 [Supabase 전환 검증 결과](Supabase_전환_실행검증_2026-11-12.json)에 별도로 보관했다.
