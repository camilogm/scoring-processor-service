import logging

from app.settings import Settings

log = logging.getLogger(__name__)


def start_debugger(settings: Settings) -> bool:
    """Open a debugpy port for an IDE to attach to; off unless DEBUGPY_PORT is set.

    Call it before the worker thread starts, so breakpoints in the pipeline hit too. A missing
    debugpy or a busy port (a second process, a reload that hasn't released it yet) is logged,
    never fatal: the debugger is a local convenience.
    """
    if not settings.debugpy_port:
        return False
    try:
        import debugpy  # noqa: T100 (opt-in, behind DEBUGPY_PORT)
    except ImportError:
        log.warning("DEBUGPY_PORT is set but debugpy isn't installed (uv sync, or build with INSTALL_DEBUGPY=true)")
        return False

    address = (settings.debugpy_host, settings.debugpy_port)
    try:
        debugpy.listen(address)  # noqa: T100
    except RuntimeError as exc:
        log.warning("debugger not started on %s:%d: %s", *address, exc)
        return False
    log.info("debugger listening on %s:%d, attach your IDE", *address)
    return True
