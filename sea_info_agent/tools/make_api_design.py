"""docs/API_설계서.md 를 서버 코드에서 바로 만들어요. (API를 바꾼 뒤 실행)

    .venv\\Scripts\\python -X utf8 tools/make_api_design.py    # 서버를 켜지 않아도 돼요

- 주소 · 요청 · 응답 모양: FastAPI가 만드는 OpenAPI 스펙
- 권한: 라우터의 Depends(require_admin / require_user / optional_user)
- 오류 응답: 라우터 함수(와 같은 파일의 도우미 함수) 안의 HTTPException
"""
# 2026.11.13 jongmin 단위 프로젝트 산출물(P1-1 API 설계 문서) 자동 생성 도구 추가
from __future__ import annotations

import ast
import collections
import datetime
import inspect
import pathlib
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.routing import APIRoute  # noqa: E402

from backend.main import app  # noqa: E402

OUT = ROOT / "docs" / "API_설계서.md"

# 그룹(tag) 순서 · 담당자 · API ID 앞글자
# 2026.11.13 jongmin 담당자를 SVN update 기준으로 수정 (나의 수족관 → 편성훈, 바다 놀이터 → 이종민)
GROUPS = [
    ("공통", "편성훈", "COM"), ("생물 알아보기", "편성훈", "IDF"), ("나의 수족관", "편성훈", "AQU"),
    ("사진", "편성훈", "PHO"),
    ("로그인", "이종민", "AUTH"), ("내 정보", "이종민", "ME"), ("관리자 운영", "이종민", "ADM"),
    ("바다 놀이터", "이종민", "GAME"),
    ("물때", "김바로", "TIDE"), ("제보·건의", "김바로", "INQ"), ("생물 이름 검색", "김바로", "SPS"),
    ("커뮤니티", "차유진", "COMM"), ("게시판 관리", "차유진", "BRD"),
    ("바다 소식", "이정민", "NEWS"), ("관리자 · 콘텐츠", "이정민", "CNT"),
]
OWNER = {g: o for g, o, _ in GROUPS}
PREFIX = {g: p for g, _, p in GROUPS}
METHOD_ORDER = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 3, "DELETE": 4}

AUTH_LABEL = {"admin": "관리자", "user": "회원", "optional": "누구나 (로그인하면 회원 기준)", "public": "누구나"}
AUTH_SHORT = {"admin": "🔴 관리자", "user": "🟠 회원", "optional": "🟢 누구나*", "public": "🟢 누구나"}


# ── 라우트 · 권한 ──────────────────────────────────────────────

def walk(routes):
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):  # FastAPI 0.16x: include_router 가 감싼 라우터
            yield from walk(r.original_router.routes)


def dep_names(dependant, acc=None) -> set[str]:
    acc = set() if acc is None else acc
    for d in dependant.dependencies:
        acc.add(getattr(d.call, "__name__", str(d.call)))
        dep_names(d, acc)
    return acc


def auth_level(route: APIRoute) -> str:
    names = dep_names(route.dependant)
    for key, fn in (("admin", "require_admin"), ("user", "require_user"), ("optional", "optional_user")):
        if fn in names:
            return key
    return "public"


# ── 오류 응답(HTTPException) 찾기 ───────────────────────────────

def _msg(node) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            out.append(v.value if isinstance(v, ast.Constant) else "{…}")
        return "".join(out)
    if isinstance(node, ast.BinOp):
        return _msg(node.left) + "…"
    if isinstance(node, ast.IfExp):
        return _msg(node.body)
    if isinstance(node, ast.Dict):  # {"code": "...", "message": "..."} 모양
        d = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
        code = d.get("code")
        code_txt = f"`{code.value}` " if isinstance(code, ast.Constant) else ""
        if "message" in d:
            return f"{code_txt}{_msg(d['message'])}"
        if len(d) == 1 and not code_txt:  # community fail({"칸": "안내"})
            k, v = next(iter(d.items()))
            return f"`{k}` 칸: {_msg(v)}"
        return f"{code_txt}(칸별 안내)".strip()
    return "(오류 내용에 따라 다름)"


def _raises(fn_node) -> list[tuple[int, str]]:
    found = []
    for n in ast.walk(fn_node):
        # community.py 의 fail({칸: 안내}, 상태) 도우미 → HTTPException(상태, {"code": "invalid_fields", ...})
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "fail" and n.args:
            st = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "status"), None)
            code = st.value if isinstance(st, ast.Constant) else 422
            found.append((code, _msg(n.args[0])))
            continue
        if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "HTTPException":
            args = list(n.args)
            kw = {k.arg: k.value for k in n.keywords}
            code = args[0] if args else kw.get("status_code")
            detail = args[1] if len(args) > 1 else kw.get("detail")
            if isinstance(code, ast.Constant) and isinstance(code.value, int):
                found.append((code.value, _msg(detail) if detail is not None else ""))
    return found


_module_cache: dict[str, dict[str, ast.FunctionDef]] = {}


def _module_funcs(module) -> dict[str, ast.AST]:
    name = module.__name__
    if name not in _module_cache:
        tree = ast.parse(inspect.getsource(module))
        _module_cache[name] = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return _module_cache[name]


def errors_of(route: APIRoute) -> list[tuple[int, str]]:
    """엔드포인트 함수 + 그 함수가 부르는 같은 파일의 도우미 함수(2단계까지)에서 HTTPException을 모아요."""
    fn = route.endpoint
    module = sys.modules[fn.__module__]
    funcs = _module_funcs(module)
    start = funcs.get(fn.__name__)
    if start is None:
        start = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    seen, todo, out = set(), [(start, 0)], []
    while todo:
        node, depth = todo.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        out += _raises(node)
        if depth >= 2:
            continue
        for n in ast.walk(node):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in funcs:
                todo.append((funcs[n.func.id], depth + 1))
    uniq = []
    for e in out:
        if e not in uniq:
            uniq.append(e)
    return sorted(uniq, key=lambda e: e[0])


# ── 스키마 → 표 ─────────────────────────────────────────────────

def ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def type_of(sch: dict) -> str:
    if not sch:
        return "any"
    if "$ref" in sch:
        n = ref_name(sch["$ref"])
        return f"[{n}](#model-{n.lower()})"
    if "anyOf" in sch:
        parts = [type_of(s) for s in sch["anyOf"]]
        nullable = "null" in parts
        parts = [p for p in parts if p != "null"]
        return " \\| ".join(parts) + (" (null 가능)" if nullable else "")
    if "enum" in sch:
        return " \\| ".join(f"`{v}`" for v in sch["enum"])
    if "const" in sch:
        return f"`{sch['const']}`"
    t = sch.get("type", "object")
    if t == "array":
        return f"{type_of(sch.get('items', {}))}[]"
    if t == "object" and "additionalProperties" in sch:
        ap = sch["additionalProperties"]
        return f"object<string, {type_of(ap) if isinstance(ap, dict) else 'any'}>"
    if t == "string" and (sch.get("format") == "binary" or "contentMediaType" in sch):
        return "file"
    if t == "string" and sch.get("format"):
        return f"string({sch['format']})"
    return t


def rules_of(sch: dict) -> str:
    keys = {"minLength": "최소 {}자", "maxLength": "최대 {}자", "minimum": "≥ {}", "maximum": "≤ {}",
            "exclusiveMinimum": "> {}", "exclusiveMaximum": "< {}", "minItems": "최소 {}개", "maxItems": "최대 {}개",
            "pattern": "형식 `{}`"}
    out = [v.format(sch[k]) for k, v in keys.items() if k in sch]
    for alt in sch.get("anyOf", []):
        out += [v.format(alt[k]) for k, v in keys.items() if k in alt]
    return ", ".join(out)


def default_of(sch: dict) -> str:
    if "default" not in sch:
        return ""
    d = sch["default"]
    if d in ("", [], {}):
        return f"`{d!r}`" if d == "" else f"`{d}`"
    if d is None:
        return "`null`"
    if isinstance(d, bool):
        return f"`{str(d).lower()}`"
    return f"`{d}`"


def cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ").strip()


def field_table(sch: dict) -> list[str]:
    props = sch.get("properties", {})
    if not props:
        return ["(필드 없음)", ""]
    req = set(sch.get("required", []))
    lines = ["| 필드 | 타입 | 필수 | 기본값 | 조건 · 설명 |", "|---|---|:-:|---|---|"]
    for name, p in props.items():
        desc = ", ".join(x for x in (rules_of(p), p.get("description", "")) if x)
        lines.append(f"| `{name}` | {type_of(p)} | {'✔' if name in req else ''} | {default_of(p)} | {cell(desc)} |")
    return lines + [""]


def body_info(op: dict, schemas: dict) -> tuple[str, str, dict] | None:
    rb = op.get("requestBody")
    if not rb:
        return None
    ctype, content = next(iter(rb["content"].items()))
    sch = content.get("schema", {})
    if "$ref" in sch:
        name = ref_name(sch["$ref"])
        return ctype, name, schemas.get(name, {})
    return ctype, "", sch


def response_info(op: dict) -> tuple[str, str]:
    for code in ("200", "201", "204"):
        r = op.get("responses", {}).get(code)
        if r is None:
            continue
        sch = r.get("content", {}).get("application/json", {}).get("schema", {})
        return code, type_of(sch) if sch else "(본문 없음)"
    return "200", "-"


# ── 문서 만들기 ─────────────────────────────────────────────────

COMMON_ERRORS = {
    "admin": [(401, "로그인이 필요해요."), (403, "관리자만 할 수 있어요. / 이용이 정지된 계정이에요.")],
    "user": [(401, "로그인이 필요해요."), (403, "이용이 정지된 계정이에요. (YYYY.MM.DD까지 · 사유)")],
}


def main() -> None:
    spec = app.openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    route_map = {}
    for r in walk(app.routes):
        for m in r.methods:
            route_map[(r.path, m)] = r

    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            m = method.upper()
            route = route_map.get((path, m))
            tag = (op.get("tags") or ["기타"])[0]
            groups[tag].append({
                "path": path, "method": m, "op": op, "tag": tag,
                "auth": auth_level(route) if route else "public",
                "errors": errors_of(route) if route else [],
                "func": f"{route.endpoint.__module__.replace('.', '/')}.py · {route.endpoint.__name__}()" if route else "",
            })
    order = [g for g, _, _ in GROUPS if g in groups] + sorted(set(groups) - set(OWNER))
    for i, tag in enumerate(order, 1):
        groups[tag].sort(key=lambda a: (a["path"], METHOD_ORDER.get(a["method"], 9)))
        for j, a in enumerate(groups[tag], 1):
            a["id"] = f"{PREFIX.get(tag, f'ETC{i}')}-{j:02d}"

    total = sum(len(v) for v in groups.values())
    auth_count = collections.Counter(a["auth"] for v in groups.values() for a in v)
    used_models = set()
    today = datetime.date.today().strftime("%Y.%m.%d")

    L: list[str] = []
    w = L.append
    w("# Sea-Info Agent API 설계서")
    w("")
    w("| 항목 | 내용 |")
    w("|---|---|")
    w("| 산출물 | P1-1 API 설계 문서 |")
    w("| 서비스 | Sea-Info Agent — 사진 한 장으로 바다 생물을 알아보고 · 모으고 · 나누는 반응형 웹 |")
    w(f"| API 버전 | {spec['info'].get('version', '')} |")
    w(f"| 작성일 | {today} (서버 코드에서 자동 생성 · `tools/make_api_design.py`) |")
    w(f"| API 수 | {total}개 ({len(order)}개 그룹) |")
    w("")
    w("## 목차")
    w("")
    w("1. [개요](#1-개요)")
    w("2. [공통 규칙](#2-공통-규칙)")
    w("3. [API 목록](#3-api-목록)")
    w("4. [API 상세](#4-api-상세)")
    w("5. [데이터 모델](#5-데이터-모델)")
    w("")

    # 1. 개요
    w("## 1. 개요")
    w("")
    w("### 1.1 시스템 구성")
    w("")
    w("```")
    w("[브라우저] ──▶ [Streamlit 화면 :8501] ──services/api.py──▶ [FastAPI 서버 :8000] ──▶ SQLite / Supabase(Postgres)")
    w("                                                                    ├──▶ Redis (세션 · 하루 분석 횟수 · 랭킹 · 조회수)")
    w("                                                                    ├──▶ Supabase Auth (가입 · 로그인 · 메일 인증)")
    w("                                                                    ├──▶ Gemini (사진 속 생물 분석)")
    w("                                                                    ├──▶ Google SMTP (알림 메일)")
    w("                                                                    └──▶ Jira (제보 · 건의 연동)")
    w("```")
    w("")
    w("- 화면(Streamlit)은 DB에 직접 붙지 않고, 모든 데이터를 이 API로 주고받아요.")
    w("- 외부 키(Supabase · Gemini · Gmail · Jira)가 없으면 **로컬 대체 모드**로 같은 API가 그대로 동작해요. 현재 모드는 `GET /health`의 `modes`로 확인해요.")
    w("")
    w("### 1.2 기본 정보")
    w("")
    w("| 항목 | 값 |")
    w("|---|---|")
    w("| Base URL (로컬) | `http://localhost:8000` (화면 쪽 환경변수 `BACKEND_URL`) |")
    w("| 대화형 문서 | `http://localhost:8000/docs` (Swagger UI) · `/redoc` · `/openapi.json` |")
    w("| 프레임워크 | FastAPI + pydantic (요청 · 응답 타입 검사) |")
    w("| 문자 인코딩 | UTF-8 |")
    w("| 날짜 · 시각 | 날짜 `YYYY-MM-DD`, 시각 ISO 8601 (`created_at`). 화면용 상대 시각(`'2시간 전'`)은 `created` |")
    w("")
    w("### 1.3 그룹과 담당")
    w("")
    w("| 그룹 | ID 접두어 | 담당 | API 수 | 주요 기능 |")
    w("|---|---|---|--:|---|")
    group_desc = {
        "공통": "서버 상태 · 공개 설정 · 생물 도감 · 회원 공개 정보",
        "생물 알아보기": "사진 분석(Gemini) · 하루 횟수 · 수족관에 담기 · 로그인 전 임시 보관",
        "사진": "읽기 권한을 확인한 뒤 관찰 · 게시글 사진 파일 내려주기",
        "로그인": "가입 · 메일 인증 · 로그인 · 비밀번호 · 프로필 · 탈퇴",
        "내 정보": "내가 쓴 글 · 댓글",
        "관리자 운영": "대시보드 · 회원 · 게시물 · 신고 · 운영 설정 · 조치 기록",
        "나의 수족관": "관찰 기록 · 생물 카드 · 수족관 공개 · 다른 사람 수족관 구경",
        "물때": "물때 지역 · 지역별 오늘 물때 · 고른 지역 기억",
        "제보·건의": "제보 · 건의 접수 · 비회원 처리 확인 · 관리자 답변 · Jira 연동(웹훅)",
        "생물 이름 검색": "국명 · 학명으로 공식 해양생물 검색 · 상세",
        "커뮤니티": "게시판 · 글 · 사진 · 댓글 · 공감 · 신고 · 조회수",
        "게시판 관리": "게시판 만들기 · 설정 · 순서 · 입력 항목 · 통계 · 변경 기록",
        "바다 놀이터": "퀴즈 문제 · 점수 기록 · 내 최고 점수 · 게임별 Top 5",
        "바다 소식": "바다 소식 목록 · 최신 · 많이 읽은 소식 · 조회수",
        "관리자 · 콘텐츠": "소식 수집 · 출처 관리 · 소식 고치기 · AI 다시 요약 · 퀴즈 문제 관리",
    }
    for tag in order:
        w(f"| [{tag}](#group-{PREFIX.get(tag, 'etc').lower()}) | `{PREFIX.get(tag, '-')}` | {OWNER.get(tag, '-')} | "
          f"{len(groups[tag])} | {group_desc.get(tag, '')} |")
    w("")

    # 2. 공통 규칙
    w("## 2. 공통 규칙")
    w("")
    w("### 2.1 인증")
    w("")
    w("- 로그인(`POST /auth/login`) · 가입 인증(`POST /auth/verify-link`)에 성공하면 응답의 `token`을 받아요.")
    w("- 로그인이 필요한 API는 요청 머리글에 토큰을 실어 보내요. 화면은 `services/api.py`가 자동으로 붙여요.")
    w("")
    w("```http")
    w("Authorization: Bearer <token>")
    w("```")
    w("")
    w("- 세션은 Redis에 보관하고, 로그아웃(`POST /auth/logout`)하면 바로 지워져요.")
    w("- 권한은 화면이 아니라 **서버에서** 확인해요 (`backend/core/security.py`).")
    w("")
    w("| 권한 표시 | 의미 | 서버 확인 함수 | 개수 |")
    w("|---|---|---|--:|")
    w(f"| 🟢 누구나 | 로그인 없이 부를 수 있어요 | - | {auth_count['public']} |")
    w(f"| 🟢 누구나* | 로그인 없이도 되지만, 로그인하면 회원 기준으로 응답해요 (예: 하루 분석 횟수, 내 좋아요 표시) | `optional_user` | {auth_count['optional']} |")
    w(f"| 🟠 회원 | 로그인한 회원만. 이용 정지 계정은 막아요 | `require_user` | {auth_count['user']} |")
    w(f"| 🔴 관리자 | 관리자 계정만 | `require_admin` | {auth_count['admin']} |")
    w("")
    w("### 2.2 요청 형식")
    w("")
    w("- 본문이 있는 요청은 `Content-Type: application/json`이 기본이에요.")
    w("- 사진을 올리는 API만 `multipart/form-data`를 써요 (`POST /identify`, 커뮤니티 사진 올리기 등).")
    w("- 목록 API의 검색 · 필터 · 쪽 번호는 쿼리 문자열로 보내요 (예: `GET /admin/members?q=바다&status=active`).")
    w("")
    w("### 2.3 응답 형식")
    w("")
    w("- 성공하면 `200 OK`와 함께 각 API의 응답 모델(JSON)을 그대로 돌려줘요. 별도의 감싸는 봉투(`{data: …}`)는 없어요.")
    w("- 처리 결과만 알리는 API는 [OkOut](#model-okout) `{\"ok\": true, \"message\": \"\"}`을 돌려줘요.")
    w("")
    w("### 2.4 오류 형식")
    w("")
    w("모든 오류는 화면에 그대로 보여줄 수 있는 한국어 문장을 `detail`에 담아요. 화면은 이 문장을 `ApiError.message`로 보여줘요.")
    w("")
    w("```json")
    w("{ \"detail\": \"로그인이 필요해요.\" }")
    w("```")
    w("")
    w("화면이 오류 종류에 따라 다르게 움직여야 할 때는 `detail`이 `code`와 `message`를 가진 객체로 와요. "
      "4장 오류 표에서 문구 앞의 `코드`가 이 값이에요.")
    w("")
    w("```json")
    w("{ \"detail\": { \"code\": \"already_reported\", \"message\": \"이미 신고한 글이에요. 운영자가 확인하고 있어요.\" } }")
    w("```")
    w("")
    w("| `code` | 뜻 |")
    w("|---|---|")
    w("| `invalid_fields` | 칸별 입력 오류. `errors`에 `{칸 이름: 안내 문구}`가 함께 와요 (커뮤니티 글쓰기 등) |")
    w("| `not_sea_creature` | 사진에서 바다 생물을 찾지 못함 (분석 횟수는 줄지 않음) |")
    w("| `read_forbidden` | 게시판 읽기 권한 없음 (`board` · `level` 함께) |")
    w("| `private` | 비공개 수족관 |")
    w("| `photo_expired` | 올려 둔 사진의 보관 시간이 지남 |")
    w("| `too_fast` | 글을 너무 빨리 연달아 올림 |")
    w("| `already_reported` | 이미 신고한 글 |")
    w("")
    w("입력값 검사(pydantic)에 실패하면 `422`와 함께 어느 칸이 잘못됐는지 목록으로 와요.")
    w("")
    w("```json")
    w("{ \"detail\": [ { \"loc\": [\"body\", \"title\"], \"msg\": \"String should have at most 50 characters\", \"type\": \"string_too_long\" } ] }")
    w("```")
    w("")
    w("| 상태 코드 | 뜻 | 언제 |")
    w("|---|---|---|")
    w("| `200` | 성공 | |")
    w("| `400` | 잘못된 요청 | 요청 내용이 규칙에 맞지 않을 때 |")
    w("| `401` | 로그인 필요 | 토큰이 없거나 만료됐을 때 |")
    w("| `403` | 권한 없음 | 관리자 전용 API, 이용 정지 계정, 남의 글 · 기록을 고치려 할 때 |")
    w("| `404` | 없음 | 찾는 글 · 기록 · 회원이 없을 때 |")
    w("| `409` | 충돌 | 이미 있는 이메일 · 닉네임, 이미 처리된 신고 등 지금 상태와 맞지 않을 때 |")
    w("| `413` | 너무 큼 | 사진 용량이 운영 설정(`max_mb`)보다 클 때 |")
    w("| `415` | 지원하지 않는 형식 | 허용하지 않은 사진 형식일 때 |")
    w("| `422` | 입력값 오류 | 필수 칸 누락 · 글자 수 초과 등 |")
    w("| `429` | 횟수 초과 | 하루 분석 횟수 · 메일 재전송 제한 |")
    w("| `500` | 서버 오류 | 처리하지 못한 오류 (`서버에 문제가 생겼어요. 잠시 뒤 다시 시도해 주세요.`) |")
    w("| `502` / `503` | 외부 연동 오류 | Gemini · Supabase · Jira · 공공 API가 응답하지 않을 때 |")
    w("")

    # 3. 목록
    w("## 3. API 목록")
    w("")
    w("권한: 🟢 누구나 · 🟢 누구나*(로그인하면 회원 기준) · 🟠 회원 · 🔴 관리자")
    w("")
    for i, tag in enumerate(order, 1):
        w(f"### 3.{i} {tag} · 담당 {OWNER.get(tag, '-')}")
        w("")
        w("| ID | 방법 | 주소 | 설명 | 권한 |")
        w("|---|---|---|---|---|")
        for a in groups[tag]:
            w(f"| [{a['id']}](#{a['id'].lower()}) | `{a['method']}` | `{a['path']}` | {cell(a['op'].get('summary', ''))} | {AUTH_SHORT[a['auth']]} |")
        w("")

    # 4. 상세
    w("## 4. API 상세")
    w("")
    for i, tag in enumerate(order, 1):
        w(f'<a id="group-{PREFIX.get(tag, "etc").lower()}"></a>')
        w("")
        w(f"### 4.{i} {tag}")
        w("")
        for a in groups[tag]:
            op = a["op"]
            w(f'<a id="{a["id"].lower()}"></a>')
            w("")
            w(f"#### {a['id']} {cell(op.get('summary', ''))}")
            w("")
            w("| 항목 | 내용 |")
            w("|---|---|")
            w(f"| 요청 | `{a['method']} {a['path']}` |")
            w(f"| 권한 | {AUTH_LABEL[a['auth']]} |")
            w(f"| 담당 · 코드 | {OWNER.get(tag, '-')} · `{a['func']}` |")
            if op.get("description"):
                w(f"| 설명 | {cell(op['description'])} |")
            w("")

            params = [p for p in op.get("parameters", []) if p.get("name", "").lower() != "authorization"]
            if params:
                w("**요청 파라미터**")
                w("")
                w("| 이름 | 위치 | 타입 | 필수 | 기본값 | 조건 · 설명 |")
                w("|---|---|---|:-:|---|---|")
                where = {"path": "경로", "query": "쿼리", "header": "머리글", "cookie": "쿠키"}
                for p in params:
                    sch = p.get("schema", {})
                    desc = ", ".join(x for x in (rules_of(sch), p.get("description", "")) if x)
                    w(f"| `{p['name']}` | {where.get(p['in'], p['in'])} | {type_of(sch)} | {'✔' if p.get('required') else ''} | "
                      f"{default_of(sch)} | {cell(desc)} |")
                w("")

            bi = body_info(op, schemas)
            if bi:
                ctype, name, sch = bi
                title = f"**요청 본문** · `{ctype}`"
                if name and not name.startswith("Body_"):
                    title += f" · [{name}](#model-{name.lower()})"
                    used_models.add(name)
                w(title)
                w("")
                L.extend(field_table(sch))

            code, rtype = response_info(op)
            w(f"**응답** · `{code}` · {rtype}")
            w("")

            errs = list(COMMON_ERRORS.get(a["auth"], []))
            errs += [e for e in a["errors"] if e not in errs]
            if bi:
                errs.append((422, "입력값 검사 실패 (2.4 참고)"))
            if errs:
                w("**오류 응답**")
                w("")
                w("| 코드 | `detail` 문구 |")
                w("|---|---|")
                for c, msg in sorted(errs, key=lambda e: e[0]):
                    w(f"| `{c}` | {cell(msg)} |")
                w("")
            w("---")
            w("")

    # 5. 모델
    w("## 5. 데이터 모델")
    w("")
    w("요청 · 응답 JSON의 모양이에요. 코드 위치는 `backend/schemas/` 담당자별 파일이에요.")
    w("")
    skip = {"HTTPValidationError", "ValidationError"}
    for name in sorted(schemas, key=str.lower):
        if name in skip or name.startswith("Body_"):
            continue
        sch = schemas[name]
        w(f'<a id="model-{name.lower()}"></a>')
        w("")
        w(f"### {name}")
        w("")
        if sch.get("description"):
            w(cell(sch["description"]))
            w("")
        if sch.get("enum"):
            w("값: " + ", ".join(f"`{v}`" for v in sch["enum"]))
            w("")
            continue
        L.extend(field_table(sch))

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)} 를 만들었어요. API {total}개 · 모델 {len(schemas)}개")


def anchor(text: str) -> str:
    """GitHub 식 머리글 링크 (소문자, 공백→-, 기호 제거)."""
    out = []
    for ch in text.lower():
        if ch.isalnum() or ch in "-_":
            out.append(ch)
        elif ch == " ":
            out.append("-")
    return "".join(out)


if __name__ == "__main__":
    main()
