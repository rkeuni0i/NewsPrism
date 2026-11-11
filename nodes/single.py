"""단독 보도(같은 사건을 다룬 기사가 1건뿐) 경로.
비교할 대상이 없으므로 ① 기사의 주장을 뽑고 ② 다른 언론사의 관련 보도를 찾아 교차 확인하고 ③ 확인된 부분·확인되지 않은 부분·배경·다른 관점을
함께 담은 '새 요약 기사'를 쓴 뒤 ④ 검증한다. 원 기사가 틀렸다고 판정하지 않는다.
외부 자료는 뉴스 검색 결과에 한정된다. 공식 문서·법령·통계 원자료는 직접 확인하지 않는다."""
from concurrent.futures import ThreadPoolExecutor
import llm, prompts
from state import BiasCheck, CrossCheck, Rubric, SingleArticle, SourceCheck
from nodes.check_bias import _norm, _verify
from nodes.collect import clean_query
from tools import news_search as ns

MAX_SOURCES = 8      # 교차 확인에 쓰는 다른 언론사 기사 수
MIN_TITLE_SCORE = 0.15  # 기사 제목이 주장·원 제목과 이만큼 겹쳐야 후보로 쓴다
SECTIONS = ("report", "crosscheck", "context", "perspectives")


def single_factcheck(state: dict) -> dict:
    """기사의 핵심 주장과 교차 확인용 검색어를 뽑고, 근거 문장이 본문에 실제로 있는지 코드로 확인한다."""
    a = state["relevant"][0]
    r = llm.structured(prompts.SINGLE_CHECK.format(outlet=a.outlet, title=a.title, body=a.body[:6000]), SourceCheck)
    claims = []
    for c in r.claims:
        q = c.quote.strip()
        claims.append({**c.model_dump(), "grounded": bool(q) and q in a.body, "cross": None})  # 근거 문장이 본문에 없으면 환각으로 보고 제외
    return {"single_claims": claims, "single_queries": [q.strip() for q in r.queries if q.strip()]}


def _find_sources(a, claims: list[dict], queries: list[str]) -> list:
    """다른 언론사의 관련 기사를 찾아 본문까지 가져온다. 같은 언론사 기사는 독립적인 확인이 아니므로 뺀다."""
    queries = (queries or [])[:4] or [clean_query(a.title)]

    def search(q):
        try:
            return ns.search(q, limit=40)
        except Exception:  # noqa: BLE001  검색 하나가 실패해도 나머지로 진행한다
            return []

    with ThreadPoolExecutor(3) as ex:
        pool = [it for res in ex.map(search, queries) for it in res]
    target = ns._bigrams(" ".join([a.title] + [c["claim"] for c in claims if c["grounded"]]))
    best: dict[str, tuple[float, dict]] = {}
    for it in pool:
        o = it.get("outlet")
        if not o or o == a.outlet or o in ns.PHOTO_ONLY:
            continue
        x = ns._bigrams(it["title"])
        score = len(x & target) / len(x) if x else 0.0
        if score >= MIN_TITLE_SCORE and (o not in best or score > best[o][0]):
            best[o] = (score, it)
    picked = [it for _, it in sorted(best.values(), key=lambda v: -v[0])][:MAX_SOURCES]
    arts = ns.build_articles(picked) if picked else []
    for i, x in enumerate(arts):
        x.id = f"s{i + 1}"  # 원 기사(n1 등)와 id가 겹치지 않게
    return arts


def _valid_cite(cite, arts) -> dict | None:
    """인용문이 그 언론사 자료의 제목이나 본문에 실제로 있으면 {outlet, quote, url}, 아니면 None (환각 방지)."""
    q = _norm(cite.quote)
    if not q:
        return None
    for x in arts:
        if x.outlet == cite.outlet and (q in _norm(x.title) or q in _norm(x.body)):
            return {"outlet": x.outlet, "quote": cite.quote.strip(), "url": x.url}
    return None


def single_research(state: dict) -> dict:
    """다른 언론사 보도를 찾아 주장별로 교차 확인하고, 배경·다른 관점을 모은다. 근거 문장이 자료에 없으면 인정하지 않는다."""
    a = state["relevant"][0]
    claims = state["single_claims"]
    ok = [c for c in claims if c["grounded"]]
    arts = _find_sources(a, claims, state.get("single_queries", [])) if ok else []
    merged = [dict(c) for c in claims]
    context = []
    if arts:
        sources = "\n\n".join(f"[{x.outlet}] ({x.body_quality}) {x.title}\n{x.body[:2200]}" for x in arts)
        numbered = "\n".join(f"{i + 1}. {c['claim']}" for i, c in enumerate(ok))
        cc = llm.structured(prompts.SINGLE_CROSS.format(outlet=a.outlet, claims=numbered, sources=sources), CrossCheck)
        if cc is not None:
            ok_idx = [i for i, c in enumerate(claims) if c["grounded"]]
            for chk in cc.checks:
                if not 1 <= chk.index <= len(ok_idx):
                    continue
                cites = [v for v in (_valid_cite(ct, arts) for ct in chk.cites) if v]
                status, note = chk.status, chk.note
                if status in ("교차 확인됨", "일부 확인", "다른 정보 있음") and not cites:
                    status, note = "확인 불가", "자료에서 근거 문장을 확인하지 못했어요."  # 근거 문장이 없는 판정은 인정하지 않는다
                merged[ok_idx[chk.index - 1]]["cross"] = {"status": status, "note": note, "cites": cites}
            for cp in cc.context:
                v = _valid_cite(cp.cite, arts)
                if v and cp.kind in ("배경", "다른 관점"):
                    context.append({"kind": cp.kind, "point": cp.point, "outlet": v["outlet"], "quote": v["quote"], "url": v["url"]})
    for c in merged:  # 판정이 비어 있는 주장은 '확인 불가'로 둔다
        if c["grounded"] and c["cross"] is None:
            c["cross"] = {"status": "확인 불가", "note": "다른 언론사의 관련 보도를 찾지 못했어요." if not arts else "자료에서 확인하지 못했어요.", "cites": []}
    return {"single_claims": merged, "single_sources": arts, "single_context": context}


def _claims_text(claims: list[dict]) -> str:
    lines = []
    for c in claims:
        if not c["grounded"]:
            continue
        cr = c.get("cross") or {}
        who = ", ".join(dict.fromkeys(ct["outlet"] for ct in cr.get("cites", []))) or "없음"
        lines.append(f"- {c['claim']} (출처 유형: {c['source_type']}) → 교차 확인: {cr.get('status', '확인 불가')} ({cr.get('note', '')}; 근거 언론사: {who})")
    return "\n".join(lines)


def _doc_text(doc: dict, with_takeaways: bool = False) -> str:
    parts = [doc["headline"]] + [doc[k] for k in SECTIONS if doc.get(k)]
    if with_takeaways:
        parts += doc.get("takeaways", [])
    return "\n".join(parts)


def write_single(state: dict) -> dict:
    """확인된 주장과 교차 확인 결과, 배경·다른 관점을 바탕으로 새 요약 기사를 쓴다. 검증이 지적한 문제가 있으면 반영해 다시 쓴다."""
    a = state["relevant"][0]
    claims = state["single_claims"]
    ok = [c for c in claims if c["grounded"]]
    if not ok:
        doc = {"headline": "새 기사를 쓰지 않았어요", "report": "본문에서 근거를 확인할 수 있는 주장을 찾지 못해 새 기사를 쓰지 않았습니다.",
               "crosscheck": "", "context": "", "perspectives": "", "takeaways": []}
        return {"single_doc": doc, "single_article": _doc_text(doc), "single_note": ""}
    bc = state.get("bias_check")
    fb = ""
    if bc and not bc.passed:
        fb = "\n[이전 초안의 지적 사항 — 반드시 고쳐라]\n- " + "\n- ".join(bc.issues) + "\n"
    ctx = state.get("single_context", [])
    background = "\n".join(f"- {c['point']} ({c['outlet']})" for c in ctx if c["kind"] == "배경") or "(없음)"
    views = "\n".join(f"- {c['point']} ({c['outlet']})" for c in ctx if c["kind"] == "다른 관점") or "(없음)"
    out = llm.structured(prompts.SINGLE_WRITE.format(outlet=a.outlet, feedback=fb, claims=_claims_text(claims), background=background, views=views), SingleArticle)
    doc = out.model_dump()
    # 안내는 LLM이 쓰지 않고 코드가 붙인다: 원문에 없는 말이라 검증기가 '근거 없음'으로 오판하기 때문
    srcs = state.get("single_sources", [])
    counts: dict[str, int] = {}
    for c in ok:
        s = (c.get("cross") or {}).get("status", "확인 불가")
        counts[s] = counts.get(s, 0) + 1
    note = f"이 내용은 {a.outlet}의 단독 보도예요. "
    note += (f"다른 언론사 {len(srcs)}곳의 관련 기사를 찾아 주장 {len(ok)}개를 점검했어요 ("
             + ", ".join(f"{k} {v}개" for k, v in counts.items()) + "). ") if srcs else "다른 언론사의 관련 보도를 찾지 못해 교차 확인은 하지 못했어요. "
    note += "다른 언론의 같은 내용은 같은 출처(해명 자료 등)에서 나왔을 수 있어, '교차 확인됨'이 곧 독립적인 확인은 아니에요. 외부 자료는 뉴스 검색 결과에 한정되며, 공식 문서·법령·통계 원자료는 직접 확인하지 않았어요. 이 요약은 원 기사가 틀렸다는 뜻이 아니라, 함께 살펴볼 만한 정보를 더한 것이에요."
    return {"single_doc": doc, "single_article": _doc_text(doc), "single_note": note,
            "retry_count": state.get("retry_count", 0) + (1 if bc else 0)}


def _evidence_view(srcs: list, claims: list[dict], ctx: list[dict]) -> list:
    """검증에 넘길 외부 자료: 앞부분 + 교차 확인에서 인용된 문장. 인용은 본문 어디에 있어도 검증 입력에 들어간다.
    (예전에는 앞 1000~2500자만 보여서, 뒤쪽에 있는 인용을 '자료에 없는 내용'으로 잘못 판정해 멀쩡한 기사를 다시 쓰게 했다)"""
    quotes: dict[str, list[str]] = {}
    for c in claims:
        for ct in (c.get("cross") or {}).get("cites", []):
            quotes.setdefault(ct["outlet"], []).append(ct["quote"])
    for x in ctx:
        quotes.setdefault(x["outlet"], []).append(x["quote"])
    out = []
    for x in srcs:
        qs = list(dict.fromkeys(quotes.get(x.outlet, [])))
        out.append(x.model_copy(update={"body": x.body[:1200] + ("\n[인용된 문장] " + " / ".join(qs) if qs else "")}))
    return out


def check_single(state: dict) -> dict:
    """새 기사를 두 방향으로 검증: 원문·자료에 근거가 있는가(주장 단위) + 표현·출처 표기·왜곡 여부."""
    a = state["relevant"][0]
    if not any(c["grounded"] for c in state["single_claims"]):
        return {"bias_check": BiasCheck(passed=True, issues=[]), "claims": []}
    srcs = _evidence_view(state.get("single_sources", []), state["single_claims"], state.get("single_context", []))
    doc = state["single_doc"]
    body_text = state["single_article"]          # 제목 + 4개 본문 섹션 (생각해 볼 점은 질문이라 주장 검증에서 뺀다)
    full_text = _doc_text(doc, with_takeaways=True)
    source_text = a.body[:3500] + "\n\n[추가 자료]\n" + "\n".join(f"[{x.outlet}] {x.title} {x.body}" for x in srcs)
    with ThreadPoolExecutor(2) as ex:
        f1 = ex.submit(_verify, {"relevant": [a] + list(srcs), "summary": body_text})
        f2 = ex.submit(lambda: llm.structured(prompts.BIAS_SINGLE.format(article=full_text, source=source_text), Rubric))
        (claims, g_issues), rub = f1.result(), f2.result()
    issues = list(g_issues)
    asserted = [c for c in claims if c.stated_as_fact]
    for c in asserted:  # 단독 보도 내용과 외부 자료의 내용은 전부 출처를 붙여 써야 한다
        issues.append(f"출처 없이 단정형으로 서술: {c.claim}")
    attribution = rub.attribution_ok and not asserted
    grounded = not g_issues
    passed = grounded and rub.lexical_ok and attribution and rub.selection_ok
    bc = BiasCheck(passed=passed, issues=issues + rub.issues, lexical_ok=rub.lexical_ok,
                   attribution_ok=attribution, selection_ok=rub.selection_ok, grounded_ok=grounded)
    return {"bias_check": bc, "claims": claims}


def route_after_single(state: dict) -> str:
    if state["bias_check"].passed or state.get("retry_count", 0) >= 2:
        return "build_single"
    return "write_single"


def build_single(state: dict) -> dict:
    a = state["relevant"][0]
    out = ["# 단독 보도 팩트체크", "", f"원문: {a.outlet} - {a.title} ({a.url})", "", state.get("single_article", ""), "", "## 팩트체크"]
    for c in state["single_claims"]:
        cr = c.get("cross") or {}
        out.append(f"- [{'근거 확인' if c['grounded'] else '본문 근거 없음'} / {cr.get('status', '-')}] {c['claim']} ({c['source_type']})")
    return {"report": "\n".join(out)}
