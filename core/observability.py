"""Application observability bootstrap: logging + LangSmith."""

from core.config import settings
from core.logger import get_logger, log_extra, setup_logging

logger = get_logger("observability")


def init_observability() -> None:
    setup_logging(settings.log_level)
    settings.configure_langsmith()
    if settings.langsmith_enabled:
        logger.info(
            "LangSmith tracing enabled",
            extra=log_extra(project=settings.langsmith_project),
        )
    else:
        logger.info("LangSmith tracing disabled")
