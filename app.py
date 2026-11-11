"""FastAPI 진입점. 실행: uvicorn app:app --reload"""
import hashlib, threading, time, uuid
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import categories, db, llm, topic_summary
from graph import build
from tools import news_search as ns

app = FastAPI(title="news-frame-compare")
graph = build()
db.init()
JOBS: dict[str, dict] = {}

STEP_LABEL = {
    "collect_articles": "기사 수집",
    "judge_fan": "기사 수집",
    "judge_topic": "같은 주제 판정",
    "filter_articles": ["같은 주제 판정", "기사별 분석"],  # 판정과 분석을 동시에 돌리고, 둘 다 여기서 끝난다
    "analyze_article": "기사별 분석",
    "compare_articles": "비교",
    "write_summary": "중립 요약 작성",
    "check_bias": "팩트·편향 검증",
    "build_report": "리포트 조립",
    "no_report": "리포트 조립",
    "single_factcheck": "팩트체크",
    "single_research": "교차 확인",
    "write_single": "기사 새로 작성",
    "check_single": "표현·근거 검증",
    "build_single": "리포트 조립",
}
STEPS_SINGLE = ["기사 수집", "같은 주제 판정", "팩트체크", "교차 확인", "기사 새로 작성", "표현·근거 검증", "리포트 조립"]  # 단독 보도 경로
STEPS = ["기사 수집", "같은 주제 판정", "기사별 분석", "비교", "중립 요약 작성", "팩트·편향 검증", "리포트 조립"]


def _dump(o):
    if isinstance(o, list):
        return [_dump(x) for x in o]
    return o.model_dump() if hasattr(o, "model_dump") else o


def _art(a):
    return a.model_dump(exclude={"body"})  # 화면에는 본문이 필요 없으므로 제외 (응답을 가볍게)


def _result(query: str, final: dict) -> dict:
    """그래프 상태 -> 화면용 결과. 실행 도중의 상태로 만들면 '부분 결과'가 된다."""
    single = final.get("single_claims")
    return {
        "mode": "single" if single is not None else "compare",
        "single": {"claims": single or [], "doc": final.get("single_doc"), "note": final.get("single_note", ""),
                   "context": final.get("single_context", []),
                   "sources": [_art(x) for x in final.get("single_sources", [])]} if single is not None else None,
        "query": query,
        "anchor": final.get("anchor", ""),
        "articles": [_art(a) for a in final.get("relevant", [])],
        "excluded": [j.model_dump() for j in final.get("judgements", []) if not j.same_topic],
        "all_articles": [_art(a) for a in final.get("articles", [])],
        "notes": [{"id": x.article_id, "claim": x.main_claim} for x in final.get("analyses", [])
                  if x.article_id in {a.id for a in final.get("relevant", [])}],  # 사이드바 '관련 기사'의 한 줄 요약
        "comparison": _dump(final["comparison"]) if final.get("comparison") else None,
        "summary": final.get("summary", ""),
        "claims": _dump(final.get("claims", [])),
        "bias_check": _dump(final["bias_check"]) if final.get("bias_check") else None,
        "retry_count": final.get("retry_count", 0),
        "message": final.get("report", "") if not final.get("comparison") else "",
    }


def _run(job_id: str, query: str, seeds: list | None = None, hid: str | None = None):
    job = JOBS[job_id]
    final = {}
    try:
        for mode, data in graph.stream({"query": query, "seeds": seeds or []}, stream_mode=["updates", "values"]):
            if mode == "updates":
                for node in data:
                    if node == "single_factcheck":  # 단독 보도로 판정되면 화면의 진행 단계를 이 경로의 단계로 바꾼다
                        job["steps"] = STEPS_SINGLE
                    label = STEP_LABEL.get(node)
                    for lb in (label if isinstance(label, list) else [label]):  # 노드 하나가 화면의 여러 단계를 끝낼 수 있다
                        if lb and lb not in job["done"]:
                            job["done"].append(lb)
                    job["log"].append(node)
            else:
                final = data
                job["partial"] = _result(query, final)  # 단계가 끝날 때마다 지금까지의 결과를 화면에 줄 수 있게 저장
        res = _result(query, final)
        res["created_at"], res["hid"] = time.time(), hid
        job["result"] = res
        job["status"] = "done"
        try:
            if res.get("comparison") or res.get("single"):  # '비교 불가' 같은 결과는 저장하지 않는다(일시 오류일 수 있어 12시간 동안 재사용되면 안 된다)
                db.save(job_id, query, hid, res)  # 저장에 실패해도 화면에는 결과가 나가도록 분리
        except Exception as e:  # noqa: BLE001
            print("분석 결과 저장 실패:", e)
    except Exception as e:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = str(e) if isinstance(e, llm.LLMAuthError) else f"{type(e).__name__}: {e}"


class AnalyzeReq(BaseModel):
    query: str
    hid: str | None = None  # 주요 사건 카드에서 시작하면 그 카드의 id
    refresh: bool = False   # True면 저장된 결과를 쓰지 않고 새로 분석


def _daily_loop():
    """분야별 목록을 하루 한 번 미리 만들어 두고(정치·사회 분류, 대표 사진), 상위 5건의 요약도 미리 만든다.
    30분마다 날짜가 바뀌었는지 확인하며, 바뀌었으면 새로 만든다. 서버가 뜨자마자도 한 번 실행된다."""
    while True:
        for cat in categories.SECTIONS:
            try:
                items, _ = categories.daily(cat)
                for it in items[:5]:
                    try:
                        topic_summary.summarize(it)
                    except Exception:  # noqa: BLE001  요약 하나가 실패해도 계속
                        pass
            except Exception as e:  # noqa: BLE001
                print("분야별 갱신 실패:", cat, e)
        time.sleep(1800)


threading.Thread(target=_daily_loop, daemon=True).start()


@app.get("/")
def index():
    return FileResponse("static/index.html")


HEADLINES: dict[str, dict] = {}  # id -> 항목 (요약 요청 시 본문을 찾기 위해 보관)


@app.get("/api/headlines")
def api_headlines(limit: int = 5, refresh: bool = False, category: str = "all"):
    out = []
    for x in categories.headlines_for(category, min(max(limit, 1), 20), refresh=refresh):
        hid = hashlib.md5((x.get("url") or x["title"]).encode()).hexdigest()[:12]
        HEADLINES[hid] = x
        out.append({"id": hid, "title": x["title"], "outlet": x["outlet"], "url": x.get("url", ""),
                    "published": x["published"], "outlets": x["outlets"], "count": x["count"],
                    "thumbnail": x.get("thumbnail", ""), "labels": x.get("labels", [])})
    # 화면의 "MM/DD HH:mm 기준"에 쓸, 데이터를 실제로 받아 온 시각
    return JSONResponse(out, headers={"X-Fetched-At": str(int(ns.HEADLINES_AT["at"] * 1000))})


@app.get("/api/summary/{hid}")
def api_summary(hid: str):
    if hid not in HEADLINES:
        raise HTTPException(404)
    try:
        return {"summary": topic_summary.summarize(HEADLINES[hid])}
    except Exception as e:  # noqa: BLE001  요약 실패가 화면을 막지 않도록 빈 요약 반환
        return {"summary": "", "error": f"{type(e).__name__}: {e}"}


@app.get("/api/search")
def api_search(q: str):
    if not q.strip():
        raise HTTPException(400, "empty query")
    return [{"title": x["title"], "outlet": x["outlet"], "url": x.get("url", ""), "published": x["published"]}
            for x in ns.search(q, 30)]


REUSE_SECONDS = 12 * 3600  # 같은 기사를 이 시간 안에 다시 누르면 저장된 분석 결과를 그대로 보여준다


def _finished_job(result: dict) -> dict:
    """저장된 결과로 '이미 끝난 작업'을 만든다 (진행 단계는 모두 완료로 표시)."""
    steps = STEPS_SINGLE if result.get("mode") == "single" else STEPS
    return {"status": "done", "steps": steps, "done": list(steps), "log": [], "result": result, "partial": None, "error": None}


@app.post("/api/jobs")
def create_job(req: AnalyzeReq):
    q = req.query.strip()
    if not q:
        raise HTTPException(400, "empty query")
    if not req.refresh:  # 최근에 끝난 같은 분석이 있으면 LLM을 다시 돌리지 않는다
        hit = db.find_recent(q, REUSE_SECONDS)
        if hit:
            JOBS[hit[0]] = _finished_job(hit[1])
            return {"job_id": hit[0], "reused": True}
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"status": "running", "steps": STEPS, "done": [], "log": [], "result": None, "partial": None, "error": None}
    seeds = (HEADLINES.get(req.hid) or {}).get("articles", []) if req.hid else []
    threading.Thread(target=_run, args=(job_id, q, seeds, req.hid), daemon=True).start()
    return {"job_id": job_id, "reused": False}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id in JOBS:
        return JOBS[job_id]
    saved = db.get(job_id)  # 서버를 다시 켠 뒤에도 저장된 분석은 열린다 (공유 링크)
    if saved is None:
        raise HTTPException(404)
    return _finished_job(saved)


@app.get("/api/analyses")
def api_analyses(limit: int = 20):
    """최근 분석 기록."""
    return db.recent(min(max(limit, 1), 100))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", port=8765, reload=True)
