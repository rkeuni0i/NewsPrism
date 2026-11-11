"""분석 결과 저장 (SQLite, 파이썬 내장). 서버를 껐다 켜도 결과가 남고, 같은 기사를 다시 눌러도 LLM 분석을 반복하지 않는다."""
import contextlib, hashlib, json, pathlib, re, sqlite3, threading, time

PATH = pathlib.Path(__file__).resolve().parent / "data" / "newsprism.db"
_LOCK = threading.Lock()


def _connect() -> sqlite3.Connection:
    PATH.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def qkey(query: str) -> str:
    """같은 기사로 볼 질의의 키: 말머리([속보])와 기호·공백을 없앤 뒤 해시."""
    t = re.sub(r"\[[^\]]*\]", "", query or "")
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", t).lower()
    return hashlib.md5(t.encode("utf-8")).hexdigest()


def init() -> None:
    with _LOCK, contextlib.closing(_connect()) as c, c:
        c.execute("""CREATE TABLE IF NOT EXISTS analyses (
            id TEXT PRIMARY KEY, qkey TEXT NOT NULL, query TEXT NOT NULL, hid TEXT,
            mode TEXT, created_at REAL NOT NULL, result TEXT NOT NULL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_analyses_qkey ON analyses(qkey, created_at DESC)")
    purge_unusable()


def save(job_id: str, query: str, hid: str | None, result: dict) -> None:
    with _LOCK, contextlib.closing(_connect()) as c, c:
        c.execute("INSERT OR REPLACE INTO analyses (id, qkey, query, hid, mode, created_at, result) VALUES (?,?,?,?,?,?,?)",
                  (job_id, qkey(query), query, hid, result.get("mode", "compare"), result.get("created_at") or time.time(),
                   json.dumps(result, ensure_ascii=False)))


def get(job_id: str) -> dict | None:
    with contextlib.closing(_connect()) as c:
        row = c.execute("SELECT result FROM analyses WHERE id = ?", (job_id,)).fetchone()
    return json.loads(row["result"]) if row else None


def find_recent(query: str, max_age: float) -> tuple[str, dict] | None:
    """같은 기사에 대해 max_age(초) 이내에 끝난 분석이 있으면 (id, 결과)."""
    with contextlib.closing(_connect()) as c:
        row = c.execute("SELECT id, result FROM analyses WHERE qkey = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
                        (qkey(query), time.time() - max_age)).fetchone()
    return (row["id"], json.loads(row["result"])) if row else None


def recent(limit: int = 20) -> list[dict]:
    with contextlib.closing(_connect()) as c:
        rows = c.execute("SELECT id, query, mode, created_at FROM analyses ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def purge_unusable() -> int:
    """비교 결과도 단독 보도 결과도 없는 저장본('비교 불가' 등)을 지운다. 일시 오류로 저장된 것이 재사용되는 것을 막는다."""
    with _LOCK, contextlib.closing(_connect()) as c, c:
        rows = c.execute("SELECT id, result FROM analyses").fetchall()
        bad = [r["id"] for r in rows if (lambda x: x.get("mode", "compare") == "compare" and not x.get("comparison"))(json.loads(r["result"]))]
        for i in bad:
            c.execute("DELETE FROM analyses WHERE id = ?", (i,))
    return len(bad)
