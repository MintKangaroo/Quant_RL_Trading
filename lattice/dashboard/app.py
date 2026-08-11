"""Flask 앱 팩토리.

Store 와 Clock 을 **주입받는다**. 앱이 스스로 만들면 테스트가 진짜 창고를 보게
되고, 시각은 벽시계로 굳는다 (불변식 2).
"""

from __future__ import annotations

from typing import Any

from flask import Flask, render_template
from werkzeug.exceptions import HTTPException

from lattice.dashboard.api import data_quality
from lattice.replay.clock import Clock, LiveClock
from lattice.store import ConfigNotFound, Store, StoreError


def create_app(store: Store | None = None, clock: Clock | None = None) -> Flask:
    app = Flask(__name__)
    app.config["LATTICE_STORE"] = store if store is not None else Store()
    app.config["LATTICE_CLOCK"] = clock if clock is not None else LiveClock()
    #: 한글 응답을 이스케이프하지 않는다. 사람이 읽는 JSON 이다.
    app.json.ensure_ascii = False  # type: ignore[attr-defined]

    app.register_blueprint(data_quality.bp)

    @app.get("/")
    @app.get("/data-quality")
    def data_quality_page() -> str:
        return render_template("data_quality.html")

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
