"""CLI entry for post-chat background worker."""

from core.observability import init_observability
from workers.post_chat_worker import run_forever

if __name__ == "__main__":
    init_observability()
    run_forever()
