"""Flask 앱 팩토리.

Store 와 Clock 을 **주입받는다**. 앱이 스스로 만들면 테스트가 진짜 창고를 보게
되고, 시각은 벽시계로 굳는다 (불변식 2).
"""

from __future__ import annotations

from typing import Any

import math

from flask import Flask, render_template
from flask.json.provider import DefaultJSONProvider
from werkzeug.exceptions import HTTPException

from quant_rl_trading.dashboard.api import agent_health, briefing, data_quality, trading
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
    app.json = SafeJSONProvider(app)
    app.config["QUANT_RL_STORE"] = store if store is not None else Store()
    app.config["QUANT_RL_CLOCK"] = clock if clock is not None else LiveClock()
    #: 한글 응답을 이스케이프하지 않는다. 사람이 읽는 JSON 이다.
    app.json.ensure_ascii = False  # type: ignore[attr-defined]

    app.register_blueprint(data_quality.bp)
    app.register_blueprint(agent_health.bp)
    app.register_blueprint(briefing.bp)
    app.register_blueprint(trading.bp)

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
