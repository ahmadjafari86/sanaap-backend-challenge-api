from uvicorn_worker import UvicornWorker


class ApplicationWorker(UvicornWorker):
    CONFIG_KWARGS = {
        **UvicornWorker.CONFIG_KWARGS,
        "ws": "websockets",
        "ws_max_size": 8192,
        "ws_max_queue": 16,
        "ws_ping_interval": 20,
        "ws_ping_timeout": 20,
        "proxy_headers": False,
        "lifespan": "off",
    }
