"""Flask 앱 팩토리.

Store 와 Clock 을 **주입받는다**. 앱이 스스로 만들면 테스트가 진짜 창고를 보게
되고, 시각은 벽시계로 굳는다 (불변식 2).
"""

from __future__ import annotations

import math
from typing import Any

from flask import Flask, render_template
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException

from quant_rl_trading.dashboard.api import (
    agent_health,
    ai_review,
    briefing,
    data_quality,
    headlines,
    thirteen_f,
    learning,
    market,
    system,
    trading,
)
from quant_rl_trading.dashboard.services.live_quotes import LiveQuoteCache
from quant_rl_trading.replay.clock import Clock, LiveClock
from quant_rl_trading.settings import load_env
from quant_rl_trading.store import ConfigNotFound, Store, StoreError


class SafeJSONProvider(DefaultJSONProvider):
    """``NaN`` · ``Infinity`` 를 내보내지 않는다. **JSON 에 그런 리터럴은 없다.**

    파이썬 ``json`` 은 기본으로 `NaN` 을 그냥 찍는다. 파이썬끼리는 되읽히지만
    브라우저 ``JSON.parse`` 는 거부하고, 화면에는 이렇게 뜬다:

        Unexpected token 'N', ..."ic":NaN,"passe"... is not valid JSON

    **응답 하나가 통째로 죽는다.** 못 잰 IC 하나 때문에 화면 전체가 빈다.
    한 화면만 고치면 다음에 다른 화면에서 같은 일이 난다 — 창고에는 못 잰
    값이 언제나 있고, 그것을 0 으로 채우지 않는 것이 이 프로젝트의 규칙이기
    때문이다. 그래서 직렬화 한 곳에서 막는다.

    ``null`` 로 바꾼다. 0 으로 바꾸면 "쟀는데 0" 이 되어 화면이 거짓말한다.
    """

    @classmethod
    def _finite(cls, value: Any) -> Any:
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, dict):
            return {key: cls._finite(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._finite(item) for item in value]
        return value

    def dumps(self, obj: Any, **kwargs: Any) -> str:
        return super().dumps(self._finite(obj), **kwargs)


def create_app(store: Store | None = None, clock: Clock | None = None) -> Flask:
    # API 키를 여기서 읽는다. 안 부르면 화면이 200 을 내면서 해설만 조용히
    # 빠진다 — 그건 고장이 아니라 침묵이라 아무도 눈치채지 못한다.
    load_env()
    app = Flask(__name__)
    # **템플릿을 캐싱하지 않는다.** Flask 는 debug 가 아니면 첫 렌더에서 템플릿을
    # 붙잡아 두는데, 그러면 `.html` 을 고쳐도 서버를 재시작하기 전까지 옛 화면이
    # 나온다. 정적 파일(.js/.css)은 매 요청 디스크에서 읽으므로 **새 JS + 옛 HTML**
    # 조합이 되고, 화면에는 이렇게 뜬다:
    #
    #     renderResources: Cannot set properties of null (setting 'textContent')
    #
    # 코드는 멀쩡한데 화면만 죽어서 원인을 엉뚱한 데서 찾게 된다 — 실제로 두 번
    # 겪었다. 이 화면은 로컬 운영 도구라 매 렌더의 stat() 한 번이 아깝지 않다.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.json = SafeJSONProvider(app)
    app.config["QUANT_RL_STORE"] = store if store is not None else Store()
    app.config["QUANT_RL_CLOCK"] = clock if clock is not None else LiveClock()
    # 장중 시세 캐시. **회계와 무관한 참고 값 전용**이다(services/live_quotes 참고).
    # 자격증명이 없거나 장외면 빈 결과를 돌려주므로, 여기서 실패를 따지지 않는다 —
    # 화면이 그 열을 비워 그린다.
    app.config["QUANT_RL_LIVE_QUOTES"] = LiveQuoteCache(_ls_client_factory)
    #: 한글 응답을 이스케이프하지 않는다. 사람이 읽는 JSON 이다.
    app.json.ensure_ascii = False  # type: ignore[attr-defined]

    app.register_blueprint(data_quality.bp)
    app.register_blueprint(agent_health.bp)
    app.register_blueprint(briefing.bp)
    app.register_blueprint(trading.bp)
    app.register_blueprint(market.bp)
    app.register_blueprint(headlines.bp)
    app.register_blueprint(thirteen_f.bp)
    app.register_blueprint(system.bp)
    app.register_blueprint(learning.bp)
    app.register_blueprint(ai_review.bp)

    @app.get("/")
    @app.get("/data-quality")
    def data_quality_page() -> str:
        return render_template("data_quality.html")

    @app.get("/agent-health")
    def agent_health_page() -> str:
        return render_template("agent_health.html")

    @app.get("/briefing")
    def briefing_page() -> str:
        return render_template("briefing.html")

    @app.get("/trading")
    def trading_page() -> str:
        return render_template("trading.html")

    # 별도 창으로 뜨는 화면이라 탭 줄에 없다. 트레이딩 탭의 버튼이 연다.
    @app.get("/calendar")
    def calendar_page() -> str:
        return render_template("calendar.html")

    @app.get("/market")
    def market_page() -> str:
        return render_template("market.html")

    @app.get("/headlines")
    def headlines_page() -> str:
        return render_template("headlines.html")

    @app.get("/thirteen-f")
    def thirteen_f_page() -> str:
        return render_template("thirteen_f.html")

    @app.get("/system")
    def system_page() -> str:
        return render_template("system.html")

    @app.get("/learning")
    def learning_page() -> str:
        return render_template("learning.html")

    @app.get("/ai-review")
    def ai_review_page() -> str:
        return render_template("ai_review.html")

    @app.errorhandler(HTTPException)
    def http_error(error: HTTPException) -> Any:
        return {"error": error.description, "status": error.code}, error.code

    @app.errorhandler(ConfigNotFound)
    def config_missing(error: ConfigNotFound) -> Any:
        # 임계치가 없으면 화면이 자기 숫자를 지어내는 대신 멈춘다 (불변식 10).
        return {"error": f"설정이 없다: {error}", "status": 503}, 503

    @app.errorhandler(StoreError)
    def store_error(error: StoreError) -> Any:
        return {"error": str(error), "status": 500}, 500

    return app


def _ls_client_factory():
    """장중 시세용 LS 클라이언트. **키가 없으면 None** — 대시보드는 자격증명
    없이도 떠야 한다(데모·백테스트 창고를 볼 때가 그렇다)."""
    from quant_rl_trading.collectors.ls_client import LSClient, LSCredentials

    credentials = LSCredentials.from_env(prefix="LS_")
    if not credentials.usable():
        return None
    return LSClient(credentials=credentials, live_trading=True)
