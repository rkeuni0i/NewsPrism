import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()
_client = None
_lock = threading.Lock()


def client():
    global _client
    with _lock:
        if _client is None:
            _client = genai.Client(
                vertexai=True,
                project=os.getenv("GOOGLE_CLOUD_PROJECT"),
                location=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"),
                http_options=types.HttpOptions(timeout=100_000),  # 한 요청이 멈춰 있어도 100초 뒤에는 끊는다 (ms)
            )
    return _client


MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# 일부 호출이 멈춰 있다가 한참 뒤 실패하면, 병렬 호출을 모두 기다리는 단계 전체가 그만큼 늘어진다(측정: 판정 12건 중 1건이 146초).
# 측정: 정상 호출은 3초 안팎인데, 간헐적으로 80초 가까이 멈추는 호출이 섞여 있다. 그래서 요청 크기에 맞춰 짧게 기다린 뒤 재요청한다.
HEDGE_MIN, HEDGE_MAX = float(os.getenv("LLM_HEDGE_MIN", "6")), float(os.getenv("LLM_HEDGE_MAX", "30"))
DEADLINE = float(os.getenv("LLM_DEADLINE", "130"))        # 이 시간(초)이 지나면 포기한다
ATTEMPTS = 3                                             # 실패 재시도와 추가 요청을 합쳐 최대 이 횟수까지 보낸다


AUTH_MSG = ("Google Cloud 인증에 실패했어요. 자격 증명이 만료됐거나 인증 서버에 연결하지 못한 경우예요. "
            "터미널에서 'gcloud auth application-default login'을 실행한 뒤 다시 시도해 주세요.")


class LLMAuthError(RuntimeError):
    """인증 문제는 일시 오류가 아니라 모든 호출이 실패하는 상태이므로, 기사 하나를 건너뛰지 않고 분석 전체를 멈추고 알린다."""


def is_auth_error(e: BaseException) -> bool:
    return type(e).__name__ in ("RefreshError", "DefaultCredentialsError", "TransportError") or getattr(e, "code", None) in (401, 403)


def _give_up(err):
    """재시도를 모두 쓴 뒤 마지막 오류를 올린다. 인증 오류는 원인을 알려 주는 오류로 바꾼다."""
    if err is not None and is_auth_error(err):
        raise LLMAuthError(AUTH_MSG) from err
    raise err


def hedge_for(prompt: str) -> float:
    """이 시간(초) 동안 응답이 없으면 같은 요청을 한 번 더 보낸다. 작은 요청은 짧게, 큰 요청(검증 등)은 길게."""
    return min(HEDGE_MAX, max(HEDGE_MIN, 5 + len(prompt) / 2500))


def _resilient(fn, hedge_after: float = 20.0):
    """fn()을 호출하되, 느리면 추가 요청(hedging), 실패하면 재시도, 너무 오래 걸리면 포기한다."""
    start = time.monotonic()
    ex = ThreadPoolExecutor(max_workers=ATTEMPTS)
    pending, launched, last_err = set(), 0, None
    try:
        while True:
            if launched < ATTEMPTS:
                pending.add(ex.submit(fn)); launched += 1
            left = DEADLINE - (time.monotonic() - start)
            if left <= 0:
                if last_err is not None and is_auth_error(last_err):
                    _give_up(last_err)
                raise TimeoutError(f"LLM 응답 시간 초과({DEADLINE:.0f}초)") from last_err
            done, _ = wait(pending, timeout=min(hedge_after, left), return_when=FIRST_COMPLETED)
            for f in done:
                pending.discard(f)
                try:
                    return f.result()
                except Exception as e:  # noqa: BLE001  다음 시도로 넘어간다
                    last_err = e
            if launched >= ATTEMPTS and not pending:
                _give_up(last_err)
    finally:
        ex.shutdown(wait=False)  # 늦게 오는 요청은 기다리지 않는다


def structured(prompt: str, schema, temperature: float = 0.0):
    """Pydantic 스키마로 구조화 출력."""
    def call():
        return client().models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=temperature,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        ).parsed
    return _resilient(call, hedge_for(prompt))


def text(prompt: str, temperature: float = 0.3) -> str:
    def call():
        return client().models.generate_content(
            model=MODEL, contents=prompt,
            config=types.GenerateContentConfig(temperature=temperature),
        ).text.strip()
    return _resilient(call, hedge_for(prompt))
