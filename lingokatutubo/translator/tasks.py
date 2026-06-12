try:
    from celery import shared_task
except ImportError:  # pragma: no cover - Celery is optional until deployment wiring.
    shared_task = None


def _run_job(
    job_id,
    input_file_path,
    file_type_value,
    source_language,
    target_language,
    ocr_languages,
) -> None:
    from translator.services import _get_file_type_enum, _run_translation_job

    file_type = _get_file_type_enum()(file_type_value)
    _run_translation_job(
        str(job_id),
        input_file_path,
        file_type,
        source_language,
        target_language,
        ocr_languages,
    )


if shared_task:

    @shared_task(name="translator.process_translation_job")
    def process_translation_job(
        job_id,
        input_file_path,
        file_type_value,
        source_language,
        target_language,
        ocr_languages,
    ) -> None:
        _run_job(
            job_id,
            input_file_path,
            file_type_value,
            source_language,
            target_language,
            ocr_languages,
        )

else:

    class _MissingCeleryTask:
        def delay(self, *args, **kwargs):
            raise RuntimeError(
                "Celery is not installed. Install Celery or set "
                "LINGOKATUTUBO_TASK_MODE=thread/inline."
            )

    process_translation_job = _MissingCeleryTask()
