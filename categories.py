"""뉴스 카테고리. 구글 뉴스 여러 섹션의 기사를 모아 LLM이 7개 분야 중 하나로 직접 분류하고, 하루에 한 번 분야별 목록을 만든다.
(구글 섹션 분류는 거칠어서 그대로 쓰지 않는다. 예: 소비자원 라면 시험 결과, 기업 인물 스캔들이 '비즈니스'에 들어감)"""
import datetime, json, threading
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel
import llm
from tools import news_search as ns

CATEGORIES = [("all", "주요"), ("politics", "정치"), ("economy", "경제"), ("society", "사회"), ("world", "세계"),
              ("tech", "IT·과학"), ("culture", "생활·문화"), ("sports", "스포츠")]
LABELS = {"politics": "정치", "economy": "경제", "society": "사회", "world": "세계",
          "tech": "IT·과학", "culture": "생활·문화", "sports": "스포츠"}
SECTIONS = LABELS  # 하위 호환: 분야 키 목록으로만 쓰인다

# 구글 뉴스 섹션 -> 기본 분야(힌트). 세계·스포츠 섹션은 분류가 믿을 만해서 그대로 확정하고, 나머지는 LLM이 다시 판단한다.
FEEDS = {"NATION": "사회", "BUSINESS": "경제", "WORLD": "세계", "TECHNOLOGY": "IT·과학", "SCIENCE": "IT·과학",
         "ENTERTAINMENT": "생활·문화", "HEALTH": "생활·문화", "SPORTS": "스포츠"}
TRUSTED = {"WORLD", "SPORTS"}
PER_FEED = 40   # 섹션마다 위에서 이 수만큼만 사용 (구글이 준 중요도 순서)
BATCH = 60      # LLM 한 번에 분류하는 제목 수
DAILY_N = 20    # 분야별로 저장해 두는 사건 수
STRONG_POLITICS = ("대통령", "국무회의", "청와대", "靑", "국회", "여당", "야당", "與", "野", "민주당", "국민의힘", "총리", "탄핵", "청문회")


class _Pair(BaseModel):
    primary: str
    secondary: str = ""


class _Labels(BaseModel):
    labels: list[_Pair]


PROMPT = """아래 뉴스 제목마다 주 분야(primary) 하나와, 필요할 때만 부 분야(secondary) 하나를 정하라. 분야: 정치, 경제, 사회, 세계, IT·과학, 생활·문화, 스포츠
- primary: 기사의 중심 내용에 가장 맞는 분야.
- secondary: 기사의 중심 내용이 다른 분야에도 **실제로** 걸칠 때만 적는다. 대부분의 기사는 빈 문자열이어야 하고, primary와 같은 값은 쓰지 않는다.
  예) 외국 기업인의 스캔들이 한국 기업 주가에 영향 → primary 세계, secondary 경제 / 정부의 반도체 지원책 → primary 정치, secondary IT·과학
각 줄 앞의 [ ]는 구글 뉴스가 붙인 분야로, 참고만 하라. 틀린 경우가 많다.

분류 기준 (기사의 '중심 내용'으로 판단):
- 정치: 대통령·국무회의·정부 부처의 정책과 발표·국회·정당·선거·인사청문·특검·한국의 외교·국방 정책
- 경제: 기업 실적, 증시·금융·환율·물가, 산업 동향, 부동산 시장·정책, 투자·M&A, 공정위 등 시장 규제
- 사회: 사건·사고, 재판·수사, 교육, 노동, 복지, 환경·재난·날씨, 소비자 이슈(식품 시험 결과 등), 지역 소식
- 세계: 해외 소식 전반. 외국의 정치·외교·전쟁·사건·사고와 외국 인물(정치인·기업인·유명인)의 이야기(스캔들 포함). 한국 정부의 외교 정책은 정치, 외국 기업의 실적·시장 이야기는 경제
- IT·과학: 반도체·AI·소프트웨어·통신·우주·과학 연구·신기술 등 기술 자체가 중심일 때. 기업 인물의 스캔들은 제외
- 생활·문화: 연예·방송·영화·예술·여행·음식·패션·육아, 건강·의학 정보, 한국 연예인·인물의 스캔들
- 스포츠: 경기·선수·리그
기사가 여러 분야에 걸치면 '누구·어디의 이야기인가'로 정하라: 외국 인물·외국에서 벌어진 일이 중심이면 세계, 한국 시장·한국 기업에 미치는 영향이 중심이면 경제, 기술 자체가 중심이면 IT·과학.
제목 순서대로 정확히 {n}개의 (primary, secondary)만 반환하라.

{rows}
"""


def _classify(rows: list[tuple[str, str]]) -> list:
    """rows: (제목, 구글 분야 힌트). 제목 순서대로 분야를 돌려준다. LLM이 실패한 묶음은 None으로 채운다(힌트로 대체)."""
    def one(chunk):
        numbered = "\n".join(f"{i + 1}. [{hint}] {title}" for i, (title, hint) in enumerate(chunk))
        try:
            out = llm.structured(PROMPT.format(n=len(chunk), rows=numbered), _Labels).labels
            if len(out) != len(chunk):
                raise ValueError("라벨 수 불일치")
            return [(x.primary.strip(), x.secondary.strip()) for x in out]
        except Exception:  # noqa: BLE001
            return [None] * len(chunk)
    chunks = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    with ThreadPoolExecutor(4) as ex:
        return [x for part in ex.map(one, chunks) for x in part]


def _build_all() -> tuple[dict[str, list[dict]], float]:
    """모든 섹션의 기사를 모아 중복을 없애고 분야를 정해, 분야별 목록을 만든다. (분야키 -> 목록, 데이터 수신 시각)"""
    pools, ats = [], []
    for sec in FEEDS:
        c = ns.rss_section(sec)
        pools.append([{**t, "_sec": sec} for t in c["topics"][:PER_FEED]]); ats.append(c["at"])
    uniq: list[dict] = []
    for t in ns._interleave(*pools):  # 섹션별 중요도 순서를 번갈아 섞는다
        if not any(ns._similar(t["title"], u["title"]) for u in uniq):  # 섹션이 달라도 같은 사건이면 하나로
            uniq.append(t)
    todo = [t for t in uniq if t["_sec"] not in TRUSTED]
    labs = dict(zip((t["title"] for t in todo), _classify([(t["title"], FEEDS[t["_sec"]]) for t in todo])))
    valid = set(LABELS.values())
    main: dict[str, list[dict]] = {v: [] for v in valid}  # 이 분야가 '주 분야'인 기사
    sub: dict[str, list[dict]] = {v: [] for v in valid}   # 이 분야가 '부 분야'로 걸린 기사
    for idx, t in enumerate(uniq):
        hint = FEEDS[t["_sec"]]
        prim, sec = (hint, "") if t["_sec"] in TRUSTED else (labs.get(t["title"]) or (hint, ""))
        if prim not in valid:
            prim = hint
        if sec not in valid or sec == prim:  # 부 분야는 유효하고 주 분야와 다를 때만
            sec = ""
        if t["_sec"] == "NATION" and any(w in t["title"] for w in STRONG_POLITICS):
            prim, sec = "정치", ""  # 국내 기사에 명백한 정치 단어가 있으면 LLM 결과와 상관없이 정치
        item = {k: v for k, v in t.items() if k != "_sec"}
        item["_i"] = idx  # 구글이 준 중요도 순서 (목록 정렬용, 저장 전에 지운다)
        item["labels"] = [prim] + ([sec] if sec else [])  # 카드에 "세계 · 경제"처럼 보여줄 분야 표시
        main[prim].append(item)
        if sec:
            sub[sec].append(item)
    # 각 분야 목록: 상위 5건은 주 분야 기사만(탭마다 다르게 보이도록), 6번째부터는 부 분야로 걸린 기사를 중요도 순으로 섞는다
    def build(name: str) -> list[dict]:
        head = main[name][:5]
        rest = sorted(main[name][5:] + sub[name], key=lambda x: x["_i"])
        return [{k: v for k, v in x.items() if k != "_i"} for x in (head + rest)[:DAILY_N]]
    return {key: build(name) for key, name in LABELS.items()}, min(ats)


_BUILD_LOCK = threading.Lock()


def _daily_file(cat: str):
    return ns.CACHE / f"cat_{cat}_{datetime.date.today().isoformat()}.json"


def _enrich(items: list[dict], n: int = DAILY_N) -> None:
    """저장하는 목록에 원문 주소와 대표 사진(og:image)을 채운다. 하루 한 번만 하고 결과는 캐시된다."""
    try:
        ns.ensure_thumbnails(items, n)
    except Exception:  # noqa: BLE001  사진을 못 구해도 목록은 만든다
        pass


def _ensure_today() -> None:
    if all(_daily_file(c).exists() for c in LABELS):
        return
    with _BUILD_LOCK:  # 동시에 여러 요청이 와도 한 번만 만든다
        if all(_daily_file(c).exists() for c in LABELS):
            return
        result, at = _build_all()
        for cat, items in result.items():
            _enrich(items)
            _daily_file(cat).write_text(json.dumps({"at": at, "items": items}, ensure_ascii=False), encoding="utf-8")


def daily(cat: str) -> tuple[list[dict], float]:
    """분야별 목록: 하루에 한 번만 새로 만들어 파일에 저장하고, 그날은 저장본을 쓴다. (목록, 데이터 수신 시각)"""
    _ensure_today()
    d = json.loads(_daily_file(cat).read_text(encoding="utf-8"))
    return d["items"], d["at"]


def headlines_for(cat: str, limit: int = 5, refresh: bool = False) -> list[dict]:
    if cat not in LABELS:  # '주요'(all)만 매번 새로고침 가능
        return ns.headlines(limit, refresh=refresh)
    items, at = daily(cat)  # 다른 분야는 refresh를 무시하고 하루 1회만 갱신
    ns.HEADLINES_AT["at"] = at
    return items[:limit]
