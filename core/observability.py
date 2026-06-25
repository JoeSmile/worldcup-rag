"""Application observability bootstrap: logging + LangSmith."""

from core.config import settings
from core.logger import get_logger, log_extra, setup_logging
from core.security_config import get_security_config

logger = get_logger("observability")


def _configure_langsmith_trace_sanitization() -> None:
    sec = get_security_config().security
    if not settings.langsmith_enabled or not sec.enabled:
        return
    try:
        from langsmith import Client
        from langsmith.run_trees import configure as ls_configure

        from core.security import SecurityFilter

        ls_configure(client=Client(anonymizer=SecurityFilter.sanitize_langsmith_trace))
        logger.info("LangSmith trace sanitization enabled")
    except ImportError:
        logger.warning("langsmith package unavailable; trace sanitization skipped")


def init_observability() -> None:
    setup_logging(settings.log_level)
    settings.configure_langsmith()
    if settings.langsmith_enabled:
        _configure_langsmith_trace_sanitization()
        logger.info(
            "LangSmith tracing enabled",
            extra=log_extra(project=settings.langsmith_project),
        )
    else:
        logger.info("LangSmith tracing disabled")
