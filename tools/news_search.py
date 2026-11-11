"""Google News RSS 기반 기사 수집: 키워드 검색 / 주요기사 / 원문 URL 복원 / 본문 추출."""
from html import unescape as html_unescape
import json, os, re, time, hashlib, pathlib, urllib.parse
from concurrent.futures import ThreadPoolExecutor
import feedparser, requests, trafilatura
from state import Article

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
BASE = "https://news.google.com/rss"
LOC = "hl=ko&gl=KR&ceid=KR:ko"


# 같은 언론사의 다른 표기 -> 대표 이름. 키는 공백 제거·소문자. (앞 절반은 실제 수집 점검에서 확인된 표기)
OUTLET_ALIAS = {
    "ohmy": "오마이뉴스", "ohmynews": "오마이뉴스",
    "chosunbiz": "조선비즈",
    "tv조선뉴스": "TV조선", "tvchosun": "TV조선",
    "jibs제주방송": "JIBS", "cjb(청주방송)": "CJB청주방송",
    "mbn홈페이지": "MBN",
    "한국기자협회": "기자협회보",
    "donga science": "동아사이언스".replace(" ", ""), "dongascience": "동아사이언스",
    "thekoreaherald": "코리아헤럴드", "koreaherald": "코리아헤럴드",
    "koreajoongangdaily": "코리아중앙데일리",
    # 흔한 영문 표기
    "thehankyoreh": "한겨레", "hankyoreh": "한겨레", "yonhapnewsagency": "연합뉴스", "yonhapnewstv": "연합뉴스TV",
}
# 도메인 형태로 올라오는 이름 -> 대표 이름 (끝이 일치하면 적용)
OUTLET_DOMAIN = {"dt.co.kr": "디지털타임스", "news.sbs.co.kr": "SBS"}


def norm_outlet(name: str) -> str:
    """같은 언론사의 표기 차이 통일: 'KBS 뉴스'/'KBS뉴스'/'KBS News' -> 'KBS', 'Ohmy' -> '오마이뉴스'.
    ('연합뉴스'처럼 한글 이름 일부인 '뉴스'는 유지)"""
    def alias(n: str) -> str | None:
        k = re.sub(r"\s+", "", n).lower()
        if k in OUTLET_ALIAS:
            return OUTLET_ALIAS[k]
        for dom, canon in OUTLET_DOMAIN.items():
            if k == dom or k.endswith("." + dom):
                return canon
        return None

    n = (name or "").strip()
    if alias(n):
        return alias(n)
    m = re.match(r"^(.*?)\s*(뉴스|news)$", n, re.I)
    if m and m.group(1) and (re.search(r"\s$", n[:len(m.group(1)) + 1]) or re.fullmatch(r"[A-Za-z0-9]+", m.group(1))):
        n = m.group(1).strip()
    return alias(n) or n


def _parse(url: str) -> list[dict]:
    feed = feedparser.parse(requests.get(url, headers=UA, timeout=15).content)
    out = []
    for e in feed.entries:
        outlet = (e.get("source") or {}).get("title", "")
        title = re.sub(rf"\s*-\s*{re.escape(outlet)}\s*$", "", e.title) if outlet else e.title
        outlet = norm_outlet(outlet)
        out.append({"title": title, "outlet": outlet, "gurl": e.link,
                    "published": e.get("published", ""), "snippet": re.sub("<[^>]+>", " ", e.get("summary", "")).strip()})
    return out


# ---------- SerpApi (SERPAPI_API_KEY) ----------
CACHE = pathlib.Path(__file__).resolve().parent.parent / "samples" / "cache"


def _cache_file(kind: str, key) -> pathlib.Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"{kind}_{hashlib.md5(json.dumps(key, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}.json"


def _cache_get(kind: str, key):
    f = _cache_file(kind, key)
    if f.exists() and not os.getenv("NEWS_NO_CACHE"):
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def _cache_put(kind: str, key, value) -> None:
    _cache_file(kind, key).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _serp_file(params: dict) -> pathlib.Path:
    return CACHE / (hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")


def _serp(params: dict, ttl: int | None = None) -> dict:
    """SerpApi 호출 + 파일 캐시(개발 중 호출 한도 절약). NEWS_NO_CACHE=1이면 캐시 무시. ttl(초)이 있으면 그 시간이 지난 캐시는 새로 받는다."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = _serp_file(params)
    fresh = ttl is None or (f.exists() and time.time() - f.stat().st_mtime < ttl)
    if f.exists() and fresh and not os.getenv("NEWS_NO_CACHE"):
        return json.loads(f.read_text(encoding="utf-8"))
    r = requests.get("https://serpapi.com/search.json",
                     params={**params, "api_key": os.environ["SERPAPI_API_KEY"]}, timeout=30).json()
    if r.get("error"):
        raise RuntimeError(r["error"])
    f.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
    return r


def _g_item(x: dict) -> dict:
    return {"title": x.get("title", ""), "outlet": norm_outlet((x.get("source") or {}).get("name", "")), "url": x.get("link", ""),
            "published": x.get("iso_date") or x.get("date", ""), "snippet": x.get("snippet", ""),
            "thumbnail": x.get("thumbnail", "")}


def _flatten_google(r: dict) -> list[dict]:
    out = []
    for x in r.get("news_results", []):
        for y in ([x] + (x.get("stories") or [])):  # 주요기사는 묶음(stories) 안에 여러 언론사가 있음
            if y.get("link"):
                out.append(_g_item(y))
    return out


def _search_serp_google(q: str) -> list[dict]:
    return _flatten_google(_serp({"engine": "google_news", "q": q, "gl": "kr", "hl": "ko"}))


def _search_serp_naver(q: str) -> list[dict]:
    r = _serp({"engine": "naver", "where": "news", "query": q})
    return [{"title": x.get("title", ""), "outlet": norm_outlet((x.get("news_info") or {}).get("press_name", "")),
             "url": x.get("link", ""), "published": (x.get("news_info") or {}).get("news_date", ""),
             "snippet": x.get("snippet") or x.get("description", "")} for x in r.get("news_results", [])]


def _interleave(*lists):
    out = []
    for i in range(max(map(len, lists), default=0)):
        out += [l[i] for l in lists if i < len(l)]
    return out


def search(query: str, limit: int = 30) -> list[dict]:
    """SERPAPI_API_KEY가 있으면 Google News + Naver 결과를 섞어서, 없으면 RSS."""
    if os.getenv("SERPAPI_API_KEY"):
        try:
            return _interleave(_search_serp_naver(query), _search_serp_google(query))[:limit]
        except Exception as e:
            print("SerpApi 실패, RSS로 대체:", e)
    hit = _cache_get("rss_search", query)
    if hit is None:
        hit = _parse(f"{BASE}/search?q={urllib.parse.quote(query)}&{LOC}")
        _cache_put("rss_search", query, hit)
    return [{**it, "url": ""} for it in hit][:limit]


DIGEST = re.compile(r"이 시각|헤드라인|브리핑|오늘의 (뉴스|주요)|주요 ?뉴스|조간|석간")


def _bigrams(t: str) -> set:
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _similar(a: str, b: str, th: float = 0.4) -> bool:
    x, y = _bigrams(a), _bigrams(b)
    return bool(x and y) and len(x & y) / min(len(x), len(y)) >= th


def _group_by_title(items: list[dict]) -> list[list[dict]]:
    """제목이 비슷한 기사끼리 한 묶음으로 (묶음 정보가 없는 RSS용 폴백)."""
    groups: list[list[dict]] = []
    for it in items:
        for g in groups:
            if _similar(it["title"], g[0]["title"]):
                g.append(it); break
        else:
            groups.append([it])
    return groups


def _topic(group: list[dict]) -> dict | None:
    """한 사건 묶음 -> 대표 기사 1건 + 보도 언론사 목록. '이 시각 헤드라인' 같은 종합 기사는 대표/집계에서 제외."""
    good = [g for g in group if not DIGEST.search(g["title"])]
    if not good:
        return None
    outlets = list(dict.fromkeys(g["outlet"] for g in good if g["outlet"]))
    thumb = next((g.get("thumbnail") for g in good if g.get("thumbnail")), "")
    return {**good[0], "outlets": outlets, "count": len(outlets), "thumbnail": thumb, "articles": good}


_RSS_TOP: dict = {"at": 0.0, "topics": []}


def _rss_topics() -> list[dict]:
    """구글 뉴스 RSS 주요기사를 사건별로 묶은 것 (10분 메모리 캐시)."""
    if time.time() - _RSS_TOP["at"] > 600 or os.getenv("NEWS_NO_CACHE"):
        items = [{**it, "url": "", "thumbnail": ""} for it in _parse(f"{BASE}?{LOC}")]
        _RSS_TOP.update(at=time.time(), topics=[t for t in map(_topic, _group_by_title(items)) if t])
    return _RSS_TOP["topics"]


_SECTION: dict = {}  # 구글 뉴스 섹션 -> {"at": 수신 시각, "topics": [...]}


def rss_section(section: str, refresh: bool = False) -> dict:
    """구글 뉴스 분야별(NATION/WORLD/BUSINESS/...) RSS를 사건 단위로 묶어 돌려준다 (10분 메모리 캐시)."""
    c = _SECTION.get(section)
    if refresh or not c or time.time() - c["at"] > 600 or os.getenv("NEWS_NO_CACHE"):
        items = [{**it, "url": "", "thumbnail": ""} for it in _parse(f"{BASE}/headlines/section/topic/{section}?{LOC}")]
        topics = [t for t in map(_topic, _group_by_title(items)) if t]  # 구글이 주는 순서(중요도순)를 유지
        c = _SECTION[section] = {"at": time.time(), "topics": topics}
    return c


HEADLINES_AT: dict = {"at": 0.0}  # 가장 최근 headlines()가 사용한 데이터의 수신 시각(epoch 초)


def headlines(limit: int = 5, refresh: bool = False) -> list[dict]:
    """주요 사건 상위 limit개. 같은 사건 기사는 하나로 묶고, 보도 언론사가 많은 순으로 순위를 매긴다.
    SerpApi(사진 포함)가 limit보다 적게 주면 RSS 주요기사로 보충한다."""
    topics: list[dict] = []
    params = {"engine": "google_news", "gl": "kr", "hl": "ko"}
    if refresh:
        _RSS_TOP["at"] = 0.0  # RSS 메모리 캐시도 비운다
    HEADLINES_AT["at"] = time.time()
    if os.getenv("SERPAPI_API_KEY"):
        try:
            r = _serp(params, ttl=0 if refresh else 1800)  # 주요 사건은 30분마다 갱신(새로고침이면 즉시)
            HEADLINES_AT["at"] = _serp_file(params).stat().st_mtime
            groups = []
            for x in r.get("news_results", []):  # 최상위 항목 하나 = 구글이 묶어둔 한 사건
                stories = x.get("stories") or ([x] if x.get("link") else [])
                groups.append([_g_item(y) for y in stories if y.get("link")])
            topics = [t for t in map(_topic, groups) if t]
        except Exception as e:
            print("SerpApi 실패, RSS로 대체:", e)
    topics.sort(key=lambda t: -t["count"])  # 안정 정렬: 동률이면 원래(구글) 순서 유지
    out: list[dict] = []
    def add(t):
        for o in out:
            if _similar(t["title"], o["title"]):  # 서로 다른 묶음이어도 같은 사건이면 합침
                o["outlets"] = list(dict.fromkeys(o["outlets"] + t["outlets"])); o["count"] = len(o["outlets"])
                o["thumbnail"] = o.get("thumbnail") or t.get("thumbnail", "")
                o["articles"] = o.get("articles", []) + t.get("articles", [])
                return
        out.append(dict(t))
    for t in topics:
        add(t)
    if len(out) < limit:
        for t in sorted(_rss_topics(), key=lambda t: -t["count"]):
            if len(out) >= limit:
                break
            add(t)
    out = out[:limit]
    ensure_thumbnails(out, limit)  # 순위가 밀려 RSS로 보충된 항목도 사진이 보이도록
    return out


def resolve_url(gurl: str) -> str:
    """news.google.com/rss/articles/<id> -> 원문 URL (batchexecute 방식)."""
    try:
        gid = urllib.parse.urlparse(gurl).path.rstrip("/").split("/")[-1]
        html = requests.get(f"https://news.google.com/rss/articles/{gid}?{LOC}", headers=UA, timeout=15).text
        sg = re.search(r'data-n-a-sg="([^"]+)"', html).group(1)
        ts = re.search(r'data-n-a-ts="([^"]+)"', html).group(1)
        inner = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "KR:ko", None, 1, None, None, None, None, None, 0, 1],
                                "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0], gid, int(ts), sg]
        body = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", json.dumps(inner), None, "generic"]]]))
        r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                          headers={**UA, "content-type": "application/x-www-form-urlencoded;charset=UTF-8"},
                          data=body, timeout=15)
        data = json.loads(r.text.split("\n\n")[1])
        return json.loads(data[0][2])[1]
    except Exception:
        return ""


def _embedded_body(html: bytes) -> str:
    """본문을 자바스크립트가 그리는 사이트 중 Arc(Fusion) 기반(조선일보 등)은 처음 받은 HTML에
    `Fusion.globalContent={...}`로 기사 JSON을 넣어 둔다. 거기서 문단(text)만 이어 붙인다.
    로그인 없이 받은 공개 HTML에 들어 있는 내용만 쓰며, 유료벽을 우회하지 않는다."""
    t = html.decode("utf-8", errors="ignore")
    i = t.find("Fusion.globalContent")
    if i < 0:
        return ""
    start = t.find("{", i)
    if start < 0:
        return ""
    depth, in_str, esc = 0, False, False
    for j in range(start, len(t)):  # 중괄호 균형으로 JSON 객체 끝 찾기 (문자열 안의 중괄호는 무시)
        c = t[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(t[start:j + 1])
                except Exception:
                    return ""
                paras = [re.sub(r"<[^>]+>", "", html_unescape(e.get("content") or "")).strip()
                         for e in data.get("content_elements", []) if e.get("type") == "text"]
                return "\n".join(x for x in paras if x)
    return ""


def _page_description(html: bytes) -> str:
    """<head>의 og:description(없으면 meta description)을 꺼낸다. 속성 순서가 달라도 찾도록 태그 단위로 검사."""
    text = html.decode("utf-8", errors="ignore")
    found = {}
    for tag in re.findall(r"<meta\s[^>]*>", text[:200000], re.I):
        key = re.search(r"""(?:property|name)\s*=\s*["'](og:description|description)["']""", tag, re.I)
        val = re.search(r"""content\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag, re.I)
        if key and val:
            found.setdefault(key.group(1).lower(), val.group(1) or val.group(2) or "")
    d = found.get("og:description") or found.get("description") or ""
    return re.sub(r"\s+", " ", html_unescape(d)).strip()


def page_image(url: str) -> str:
    """원문 페이지의 대표 이미지(og:image) 주소. 없으면 빈 문자열."""
    try:
        html = requests.get(url, headers=UA, timeout=15).content
    except Exception:
        return ""
    text = html.decode("utf-8", errors="ignore")[:200000]
    for tag in re.findall(r"<meta\s[^>]*>", text, re.I):
        if re.search(r"""(?:property|name)\s*=\s*["']og:image["']""", tag, re.I):
            m = re.search(r"""content\s*=\s*(?:"([^"]*)"|'([^']*)')""", tag, re.I)
            if m:
                return urllib.parse.urljoin(url, html_unescape(m.group(1) or m.group(2) or "").strip())
    return ""


def ensure_thumbnails(items: list[dict], n: int = 20) -> None:
    """사진이 없는 항목(순위가 밀려 RSS로 채워진 기사 등)에 원문 페이지의 대표 이미지(og:image)를 채운다.
    원문 주소도 함께 복원해 두며, 결과는 파일에 캐시해 같은 기사는 다시 받지 않는다."""
    def one(it):
        if it.get("thumbnail"):
            return
        key = it.get("url") or it.get("gurl") or it["title"]
        hit = _cache_get("thumb", key)
        if hit is None:
            try:
                url = it.get("url") or (resolve_url(it["gurl"]) if it.get("gurl") else "")
                hit = {"img": page_image(url) if url else "", "url": url}
            except Exception:
                return
            _cache_put("thumb", key, hit)
        it["thumbnail"] = hit.get("img", "")
        if hit.get("url") and not it.get("url"):
            it["url"] = hit["url"]
    with ThreadPoolExecutor(6) as ex:
        list(ex.map(one, items[:n]))


def extract_page(url: str) -> tuple[str, str]:
    """원문 페이지를 한 번만 받아 (본문, 페이지 요약문)을 돌려준다.
    요약문은 페이지 <head>의 og:description / meta description으로, 본문이 자바스크립트로 그려지는 사이트에서도 들어 있다."""
    try:
        html = requests.get(url, headers=UA, timeout=15).content
    except Exception:
        return "", ""
    body = ""
    try:
        body = trafilatura.extract(html, include_comments=False, favor_recall=True) or ""
    except Exception:
        pass
    if len(body) < 200:  # 일반 추출이 실패하면 페이지에 내장된 기사 JSON을 시도
        emb = _embedded_body(html)
        if len(emb) > len(body):
            body = emb
    desc = _page_description(html)
    return body, desc


def extract_body(url: str) -> str:
    return extract_page(url)[0]


# 비교 대상을 고를 때 먼저 담는 주요 언론사 (종합일간지, 방송·통신, 주요 경제지)
MAJOR_OUTLETS = {
    "경향신문", "국민일보", "동아일보", "문화일보", "서울신문", "세계일보", "조선일보", "중앙일보", "한겨레", "한국일보",
    "뉴스1", "뉴시스", "연합뉴스", "연합뉴스TV", "채널A", "한국경제TV", "JTBC", "KBS", "MBC", "MBN", "SBS", "SBS Biz", "TV조선", "YTN",
    "매일경제", "머니투데이", "서울경제", "아시아경제", "이데일리", "조선비즈", "파이낸셜뉴스", "한국경제", "헤럴드경제",
}

# 보도사진 제공 매체: 텍스트 기사가 거의 없어 기사 비교에서 제외
PHOTO_ONLY = {"신화사 연합뉴스", "AP연합뉴스", "EPA연합뉴스"}


def pick_distinct_outlets(items: list[dict], n: int = 6) -> list[dict]:
    seen, out = set(), []
    for it in items:
        if it["outlet"] and it["outlet"] not in seen and it["outlet"] not in PHOTO_ONLY:
            seen.add(it["outlet"]); out.append(it)
        if len(out) >= n:
            break
    return out


def build_articles(items: list[dict]) -> list[Article]:
    """후보 → Article. 본문 추출 실패 시 RSS 스니펫으로 폴백(body_source로 구분 불가하므로 title+snippet 사용)."""
    def one(i_it):
        i, it = i_it
        key = it.get("url") or it.get("gurl") or it["title"]
        hit = _cache_get("article", key)
        if hit:  # 이미 수집한 기사는 재수집하지 않는다 (id만 이번 순서로 갱신)
            return Article(**{**hit, "id": f"n{i+1}", "outlet": it["outlet"]})
        url = it.get("url") or resolve_url(it["gurl"])
        body, desc = extract_page(url) if url else ("", "")
        quality = "full" if len(body) >= 500 else "partial" if len(body) >= 200 else "snippet"
        if quality == "snippet":  # 유료벽/추출 실패: 우회하지 않고 제목+요약으로만 사용
            # 페이지 자체의 요약문(og:description)이 있으면 그것을, 없으면 검색 결과의 요약을 쓴다
            body = f"{it['title']}. {desc or it['snippet']}".strip()
        art = Article(id=f"n{i+1}", outlet=it["outlet"], title=it["title"], body=body[:6000], body_quality=quality,
                      url=url or it.get("gurl", ""), published=it["published"])
        if quality != "snippet":  # 추출 실패(일시 오류/유료벽)는 캐시하지 않아 다음에 재시도
            _cache_put("article", key, art.model_dump())
        return art
    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(one, enumerate(items)))
