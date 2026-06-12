import threading
from typing import Any, Callable

from django.conf import settings


def submit_translation_task(func: Callable[..., None], *args: Any) -> None:
    """Submit a translation task through the configured runner.

    The default development runner is a daemon thread. Production should set
    `LINGOKATUTUBO_TASK_MODE=celery` after a Celery worker is wired.
    """
    mode = getattr(settings, "LINGOKATUTUBO_TASK_MODE", "thread").lower()

    if mode == "inline":
        func(*args)
        return

    if mode == "celery":
        from translator.tasks import process_translation_job

        process_translation_job.delay(*args)
        return

    thread = threading.Thread(
        target=func,
        args=args,
        daemon=True,
        name=f"lingokatutubo-translation-{args[0]}",
    )
    thread.start()
