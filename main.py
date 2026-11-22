import socket
import sys

from app import config, localization
from app.logging_setup import setup


def main():
    config.ensure_dirs()
    log = setup()
    cfg = config.load()
    from app.instance import InstanceLock
    instance = InstanceLock(config.DATA_DIR)
    if not instance.acquire():
        log.error("Deban is already using this data directory.")
        sys.exit(1)
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", cfg["port"])) == 0:
            log.error(localization.message("port_in_use"), cfg["port"])
            sys.exit(1)
    from app.pipeline import pipeline
    from app.server import app
    import uvicorn
    pipeline.boot()
    log.info("http://127.0.0.1:%s", cfg["port"])
    try:
        uvicorn.run(app, host="127.0.0.1", port=cfg["port"], log_level="warning",
                    access_log=False, timeout_graceful_shutdown=3)
    finally:
        instance.release()


if __name__ == "__main__":
    main()
