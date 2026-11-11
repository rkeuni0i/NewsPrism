import re, unicodedata
from concurrent.futures import ThreadPoolExecutor
import llm, prompts
from state import BiasCheck, ClaimCheck, Rubric
from tools import news_search as ns


_CHARS = str.maketrans({"∼": "~", "～": "~", "〜": "~", "‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "..."})


def _norm(s: str) -> str:
    """인용문 비교용 정규화: 물결표·따옴표·줄임표·공백 차이를 무시한다."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "").translate(_CHARS))


def _verify(state) -> tuple[list, list[str]]:
    """SAFE 변형(arXiv 2403.18802): 요약을 원자적 주장으로 분해해 수집 기사에 대해 판정."""
    arts = state["relevant"]
    text = "\n\n".join(f"[{a.outlet}] ({a.body_quality}) {a.title}\n{a.body[:2500]}" for a in arts)
    cc = llm.structured(prompts.VERIFY.format(summary=state["summary"], articles=text), ClaimCheck)
    need = len(arts) // 2 + 1
    issues = []
    normed = [(_norm(a.title), _norm(a.body)) for a in arts]
    for c in cc.claims:
        # 근거 문장이 실제 제목이나 본문에 없으면 supported를 인정하지 않음 (환각 방지).
        # 물결표(3∼4/3~4)·따옴표·공백만 다른 것은 같은 문장으로 본다.
        q = _norm(c.quote)
        if c.verdict == "supported" and not (q and any(q in t or q in b for t, b in normed)):
            c.verdict = "unsupported"
        if c.verdict != "supported":
            issues.append(f"근거 없음/충돌 주장({c.verdict}): {c.claim}")
        elif c.unsupported_outlets:
            # 어느 언론사 표기가 틀렸고 근거가 확인된 곳은 어디인지 알려줘야, 다시 쓸 때 같은 실수를 반복하지 않는다
            ok = ", ".join(dict.fromkeys(c.supporting_outlets)) or "없음"
            issues.append(f"언론사 표기 오류: '{c.claim}' — 근거가 없는 언론사: {', '.join(c.unsupported_outlets)} (근거가 확인된 언론사: {ok}). "
                          f"해당 언론사 이름을 빼거나 근거가 확인된 언론사로 바꿔라.")
        elif c.stated_as_fact and len(set(c.supporting_outlets)) < need:
            issues.append(f"{len(set(c.supporting_outlets))}곳만 보도한 내용을 단정형으로 서술: {c.claim}")
    return cc.claims, issues


def _drop_outlet(line: str, outlet: str) -> str:
    """'A와 B는 ~' 같은 문장에서 언론사 이름 하나를 뺀다. 못 빼면 원문 그대로 돌려준다."""
    o = re.escape(outlet)
    for pat in (rf"{o}\s*(?:와|과|및|,|·)\s*", rf"\s*(?:와|과|및|,|·)\s*{o}"):
        new = re.sub(pat, "", line, count=1)
        if new != line:
            return new
    return line


MIN_LINES = 3  # 고친 뒤에도 요약이 이 문장 수 이상 남아야 한다


def auto_repair(summary: str, claims) -> tuple[str, list[str], bool]:
    """검증이 지적한 문제를 LLM 재작성 없이 코드로 고친다.
    - 근거를 확인하지 못한 주장(unsupported/contradicted): 그 문장을 뺀다.
    - 근거 없는 언론사 이름: 문장에서 이름만 뺀다.
    문장을 못 찾거나, 이름을 빼면 문장이 깨지거나, 요약이 MIN_LINES 미만으로 줄면 (원문, [], False)로 포기하고 재작성에 맡긴다."""
    lines = summary.split("\n")
    repairs, drop = [], set()

    def find(claim, must=None):
        """주장과 가장 비슷한 문장의 번호 (기준을 넘는 첫 문장이 아니라 가장 비슷한 문장. 비슷한 문장이 여러 개여도 엉뚱한 문장을 고치지 않는다)."""
        best, best_s, y = None, 0.5, ns._bigrams(claim)
        for i, ln in enumerate(lines):
            if i in drop or not ln.strip() or (must is not None and must not in ln):
                continue
            x = ns._bigrams(ln)
            s = len(x & y) / min(len(x), len(y)) if x and y else 0.0
            if s > best_s:
                best, best_s = i, s
        return best

    for c in claims:
        if c.verdict != "supported":
            i = find(c.claim)
            if i is None:
                return summary, [], False
            drop.add(i)
            repairs.append(f"근거를 확인하지 못해 뺀 문장: {lines[i].strip()}")
        elif c.unsupported_outlets:
            for o in c.unsupported_outlets:
                i = find(c.claim, o)
                if i is None:
                    return summary, [], False
                new = _drop_outlet(lines[i], o)
                if new == lines[i] or o in new:
                    # 이름만 빼면 문장이 깨진다(그 언론사가 유일한 출처이거나 지목된 언론사뿐): 문장을 통째로 뺀다
                    drop.add(i)
                    repairs.append(f"근거 없는 언론사('{o}')만 출처로 적혀 뺀 문장: {lines[i].strip()}")
                    break
                repairs.append(f"근거 없는 언론사 '{o}'을(를) 뺀 문장: {new.strip()}")
                lines[i] = new
    kept = [ln for i, ln in enumerate(lines) if i not in drop and ln.strip()]
    if len(kept) < MIN_LINES:
        return summary, [], False
    return "\n".join(kept), repairs, True


def _rubric(state) -> Rubric:
    return llm.structured(prompts.BIAS.format(summary=state["summary"],
                          comparison=state["comparison"].model_dump_json(indent=1)), Rubric)


def check_bias(state: dict) -> dict:
    with ThreadPoolExecutor(2) as ex:
        f1, f2 = ex.submit(_verify, state), ex.submit(_rubric, state)
        (claims, g_issues), rub = f1.result(), f2.result()
    # 실패 원인이 '근거 없는 주장'이나 '언론사 표기 오류'뿐이고 나머지 검사는 통과했다면, 코드로 고쳐서 바로 통과시킨다.
    # (LLM 재작성 1라운드 = 요약 + 검증 약 40~60초를 아낀다. 문장이나 이름을 빼기만 하므로 새 주장이 생기지 않는다.)
    fixable = g_issues and all(i.startswith(("근거 없음/충돌 주장", "언론사 표기 오류")) for i in g_issues)
    if fixable and rub.lexical_ok and rub.attribution_ok and rub.selection_ok:
        fixed, repairs, ok = auto_repair(state["summary"], claims)
        if ok:
            for c in claims:
                c.unsupported_outlets = []
            return {"summary": fixed, "claims": [c for c in claims if c.verdict == "supported"],
                    "bias_check": BiasCheck(passed=True, issues=[], repairs=repairs)}
    grounded = not g_issues
    passed = grounded and rub.lexical_ok and rub.attribution_ok and rub.selection_ok
    bc = BiasCheck(passed=passed, issues=g_issues + rub.issues, lexical_ok=rub.lexical_ok,
                   attribution_ok=rub.attribution_ok, selection_ok=rub.selection_ok, grounded_ok=grounded)
    return {"bias_check": bc, "claims": claims}


def route_after_bias(state: dict) -> str:
    if state["bias_check"].passed or state.get("retry_count", 0) >= 2:
        return "build_report"
    return "write_summary"
