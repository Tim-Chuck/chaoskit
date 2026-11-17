import socket
import threading
import time
from contextlib import contextmanager

import pytest
import uvicorn

from chaoskit.app import Settings, create_app
from chaoskit.demo import app as demo
from chaoskit.transport import FaultProtocol


@contextmanager
def live_server(app, protocol="h11"):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        config = uvicorn.Config(app, http=protocol, ws="none", log_level="warning", access_log=False,
                                timeout_graceful_shutdown=2)
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started:
                    break
                if not thread.is_alive():
                    pytest.fail("Server exited on startup")
                time.sleep(0.02)
            else:
                pytest.fail("Server startup timed out")
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)


@pytest.fixture(scope="session")
def upstream():
    with live_server(demo) as url:
        yield url


@pytest.fixture
def proxy(upstream, tmp_path):
    with live_server(create_app(Settings(upstream, str(tmp_path / "test.sqlite3"))), FaultProtocol) as url:
        yield url
