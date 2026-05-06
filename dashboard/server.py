"""Flask SSE server for the real-time dashboard.

Runs in a daemon thread; never touches asyncio objects.
"""
import logging
import os
import threading
import time
from pathlib import Path

from flask import Flask, Response, send_file

from dashboard.state import DashboardState

_STATIC_DIR = Path(__file__).parent / "static"

log = logging.getLogger(__name__)


def create_app(dash: DashboardState, maker_dash=None) -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC_DIR))
    # Suppress Flask request logs from flooding bot.log
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @app.get("/")
    def index():
        return send_file(_STATIC_DIR / "index.html")

    @app.get("/api/snapshot")
    def snapshot():
        return Response(dash.to_json(), content_type="application/json")

    @app.get("/stream")
    def stream():
        def _generate():
            while True:
                try:
                    yield f"data: {dash.to_json()}\n\n"
                    time.sleep(2.0)
                except GeneratorExit:
                    break

        return Response(_generate(), content_type="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    if maker_dash is not None:
        @app.get("/maker")
        def maker_index():
            return send_file(_STATIC_DIR / "maker.html")

        @app.get("/api/maker-snapshot")
        def maker_snapshot():
            return Response(maker_dash.to_json(), content_type="application/json")

        @app.get("/maker-stream")
        def maker_stream():
            def _generate():
                while True:
                    try:
                        yield f"data: {maker_dash.to_json()}\n\n"
                        time.sleep(2.0)
                    except GeneratorExit:
                        break
            return Response(_generate(), content_type="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


def start_dashboard_server(dash: DashboardState, port: int = 5050, maker_dash=None) -> threading.Thread:
    app = create_app(dash, maker_dash=maker_dash)

    def _run():
        app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)

    t = threading.Thread(target=_run, name="dashboard-server", daemon=True)
    t.start()
    log.info("Dashboard running at http://127.0.0.1:%d", port)
    return t
