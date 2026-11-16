"""바다 소식 API (이정민 담당). 조회 API 세 개(/news · /news/latest · /news/top)는 편성훈이 먼저 만들었고 홈도 같이 써요.

조회수는 Redis에 있어요. 같은 사람(회원은 계정, 비회원은 브라우저 + IP)의 같은 소식은 24시간에 한 번만 세요.
‘많이 읽은 소식’ 순서는 1시간마다 새로 매겨요 (그 사이 숨긴 소식은 바로 빠져요).
관리자 검토 · 직접 등록 · 수집은 backend/routers/admin_contents.py 에 있어요.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from backend.core import redis_store
from backend.core.convert import news_out
from backend.core.security import optional_user, viewer_key
from backend.core.timefmt import now, today
from backend.db import get_session
from backend.models import News, NewsRun, User
from backend.schemas.common import NewsOut
from backend.schemas.news import LastRunOut, NewsViewOut, RecommendedNewsOut

router = APIRouter(tags=["바다 소식"])
TOP_CACHE = "news:top_ids:v2"  # 많이 읽은 순서 (1시간 보관)


def _is_example_out(item: NewsOut) -> bool:
    """초기 시연용 예시 기사 여부를 판별한다."""
    url = item.url or ""
    src = item.source or ""
    return url.startswith(("https://example.com/", "http://example.com/")) or src.startswith("예시 ")


def _public(session: Session) -> list[NewsOut]:
    return [news_out(n) for n in session.exec(select(News).where(News.public == True)).all()]  # noqa: E712


def forget_top() -> None:
    """소식을 숨기거나 새로 넣은 뒤 ‘많이 읽은 소식’ 순서를 다시 매기게 해요."""
    try:
        redis_store.r().delete(f"cache:{TOP_CACHE}")
        redis_store.r().delete("cache:news:top_ids")
    except Exception:
        pass


@router.get("/news", response_model=list[NewsOut], summary="공개된 바다 소식")
def news_list(session: Session = Depends(get_session)):
    return sorted(_public(session), key=lambda n: n.date, reverse=True)


@router.get("/news/latest", response_model=list[NewsOut], summary="최신 소식 n개")
def latest(n: int = 3, session: Session = Depends(get_session)):
    rows = _public(session)
    real_rows = [x for x in rows if not _is_example_out(x)] or rows
    return sorted(real_rows, key=lambda x: x.date, reverse=True)[:n]


@router.get("/news/top", response_model=list[NewsOut], summary="많이 읽은 소식 n개 (최근 30일 · 1시간마다 순서 갱신)")
def top(n: int = 5, session: Session = Depends(get_session)):
    all_rows = _public(session)
    # 실제 수집된 소식이 있으면 예시 기사를 제외하고 우선 노출
    real_rows = [x for x in all_rows if not _is_example_out(x)]
    rows = real_rows if real_rows else all_rows
    if not rows:
        return []

    since = f"{today() - timedelta(days=30):%Y-%m-%d}"
    recent = [x for x in rows if x.date >= since]
    # 최근 30일 소식이 n개보다 적으면 전체 보유 기사에서 보충해 빈칸이 생기지 않게 처리
    pool = recent if len(recent) >= n else rows
    by_id = {x.id: x for x in rows}

    # 조회수 높은 순 -> 최신 날짜 순 -> ID 순으로 정렬 (조회수가 0이어도 일관되게 채워짐)
    sorted_pool = sorted(pool, key=lambda x: (-x.views, x.date, x.id), reverse=False)
    sorted_pool = sorted(pool, key=lambda x: (x.views, x.date), reverse=True)

    try:
        order = redis_store.cache_get(TOP_CACHE)
        valid_cached = [i for i in (order or []) if i in by_id]
        if not valid_cached:
            order = [x.id for x in sorted_pool]
            redis_store.cache_set(TOP_CACHE, order, ttl=3600)
    except Exception:  # Redis가 잠깐 안 되면 지금 조회수로 바로 매겨요
        order = [x.id for x in sorted_pool]

    ranked = [by_id[i] for i in order if i in by_id]
    known = {x.id for x in ranked}
    # 부족한 개수는 나머지 보유 기사에서 조회수/최신순으로 채움
    rest = [x for x in sorted(rows, key=lambda x: (x.views, x.date), reverse=True) if x.id not in known]
    return (ranked + rest)[:n]


# 2026.11.12 jungmin 확정된 열 개 주제별로 공개 기사를 한 건씩 추천해요.
RECOMMEND_CACHE = "news:recommended_ids:daily:v6"
RECOMMEND_TOPICS = (
    ("해양생물·생태", ("해양생물", "산호초", "생태계", "생물다양성", "고래", "바다거북")),
    ("해양환경·기후", ("해양오염", "해수온도", "해수온", "기후변화", "해양보호", "미세플라스틱")),
    ("수산·양식", ("수산업", "양식업", "어업기술", "수산물", "양식장")),
    ("어촌·수산정책", ("어촌경제", "어업인 지원", "수산정책", "어촌", "어업인")),
    ("해운·선박", ("선박", "해운산업", "해상운송", "조선", "친환경 선박")),
    ("항만·물류", ("항만운영", "해상물류", "컨테이너", "공급망", "항만", "물류")),
    ("해양안전·재난", ("해양사고", "해상구조", "안전관리", "해양재난", "선박안전")),
    ("해양과학·연구", ("해양조사", "해양관측", "연구성과", "해저탐사", "해양과학")),
    ("해양기술·산업", ("해양로봇", "스마트 기술", "해양산업", "해양기술", "자율운항")),
    ("해양에너지·자원", ("해상풍력", "조력발전", "해양자원", "해양에너지", "조류발전")),
)


def _recommend_score(news: News, words: tuple[str, ...]) -> int:
    """주제어가 제목·요약에 있으면 후보이며, 제목의 직접성을 더 높게 매겨요."""
    title = news.title.casefold()
    summary = news.summary.casefold()
    # 2026.11.12 jungmin 기관명과 출처명은 주제 점수에 포함하지 않아요.
    for name in (news.source, "해양수산부", "국립수산과학원", "국립해양생물자원관", "정책브리핑"):
        if name:
            title = title.replace(name.casefold(), "")
            summary = summary.replace(name.casefold(), "")
    keys = tuple(word.casefold() for word in words)
    return (4 if any(word in title for word in keys) else 0) + (2 if any(word in summary for word in keys) else 0)


def _recommend_order(rows: list[News]) -> list[tuple[str, str]]:
    """AI 요약 우선, 관련성 점수·게시 날짜 순으로 주제마다 한 건씩 선정해요."""
    selected: list[tuple[str, str]] = []
    used: set[str] = set()
    for category, words in RECOMMEND_TOPICS:
        candidates = [n for n in rows if n.id not in used and _recommend_score(n, words)]
        if not candidates:
            continue
        winner = max(candidates, key=lambda n: (bool(n.ai), _recommend_score(n, words), n.date, n.id))
        selected.append((category, winner.id))
        used.add(winner.id)
    return selected


def _recommend_cache_window(session: Session) -> tuple[str, int]:
    """한국 시간 오전 6시 수집 주기와 마지막 성공 수집 기록에 캐시를 맞춰요."""
    # 2026.11.12 jungmin 수집이 6시 이후 끝나도 새 기록이 생기면 그날 추천을 다시 선정해요.
    current = now()
    six_am = current.replace(hour=6, minute=0, second=0, microsecond=0)
    cycle_start = six_am if current >= six_am else six_am - timedelta(days=1)
    cycle_end = cycle_start + timedelta(days=1)
    latest_run = max(session.exec(select(NewsRun).where(NewsRun.ok > 0)).all(),
                     key=lambda run: (run.date, run.id or 0), default=None)
    key = f"{RECOMMEND_CACHE}:{cycle_start:%Y%m%d}:{latest_run.id if latest_run else 'none'}"
    return key, max(1, int((cycle_end - current).total_seconds()) + 1)


@router.get("/news/recommended", response_model=list[RecommendedNewsOut], summary="열 주제별 추천 소식")
def recommended(session: Session = Depends(get_session)) -> list[RecommendedNewsOut]:
    # 2026.11.12 jungmin 매 요청마다 공개 상태와 예시 여부를 다시 확인해 숨긴 기사는 즉시 제외해요.
    rows = session.exec(select(News).where(News.public == True)).all()  # noqa: E712
    rows = [n for n in rows if not n.url.startswith(("https://example.com/", "http://example.com/"))
            and not n.source.strip().startswith("예시")]
    by_id = {n.id: n for n in rows}
    cache_key, cache_ttl = _recommend_cache_window(session)
    try:
        cached = redis_store.cache_get(cache_key)
    except Exception:
        cached = None
    if isinstance(cached, list):
        chosen = [(category, news_id) for category, news_id in cached
                  if category in dict(RECOMMEND_TOPICS) and news_id in by_id]
        # 공개 취소나 삭제로 빈 슬롯이 생기면 그 주제의 다음 후보를 즉시 선택해요.
        if len(chosen) != len(cached):
            chosen = _recommend_order(rows)
    else:
        chosen = _recommend_order(rows)
        try:
            redis_store.cache_set(cache_key, chosen, ttl=cache_ttl)
        except Exception:
            pass
    return [RecommendedNewsOut(**news_out(by_id[news_id]).model_dump(), category=category)
            for category, news_id in chosen]


@router.get("/news/last-run", response_model=Optional[LastRunOut], summary="마지막으로 새 소식을 모은 때 (없으면 null)")
def last_run(session: Session = Depends(get_session)):
    """새 소식이 한 건이라도 들어온 마지막 수집이에요. 모두 실패한 수집은 건너뛰어요 (이전 소식이 그대로 보이니까요)."""
    run = max(session.exec(select(NewsRun).where(NewsRun.ok > 0)).all(), key=lambda r: (r.date, r.id or 0), default=None)
    return LastRunOut(date=run.date, ok=run.ok, fail=run.fail) if run else None


def _visible(news_id: str, user: User | None, session: Session) -> News:
    n = session.get(News, news_id)
    if not n or (not n.public and not (user and user.role == "admin")):
        raise HTTPException(404, "지금은 볼 수 없는 소식이에요. 목록에서 다른 소식을 골라 주세요.")
    return n


@router.get("/news/{news_id}", response_model=NewsOut, summary="소식 하나 (숨긴 소식은 관리자만)")
def news_one(news_id: str, user: User | None = Depends(optional_user), session: Session = Depends(get_session)):
    return news_out(_visible(news_id, user, session))


@router.post("/news/{news_id}/view", response_model=NewsViewOut, summary="상세를 열면 조회수 +1 (같은 사람은 24시간에 한 번)")
def view(news_id: str, request: Request, user: User | None = Depends(optional_user),
         session: Session = Depends(get_session)):
    n = _visible(news_id, user, session)
    try:
        counted = redis_store.view_once("news", n.id, viewer_key(request, user))
        return NewsViewOut(views=redis_store.views("news", n.id), counted=counted)
    except Exception as e:
        print(f"[조회수 오류] {e}", flush=True)
        raise HTTPException(503, "조회수를 세지 못했어요. 소식은 그대로 읽을 수 있어요.")
