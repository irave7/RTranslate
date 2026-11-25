"""Validate the explicitly configured relay before native translation."""
from contextlib import asynccontextmanager

from pdf2zh_next.config.translate_engine_model import FreeLLMAPISettings


@asynccontextmanager
async def resolve_managed_gateway(settings):
    engines = (settings.translate_engine_settings, settings.term_extraction_engine_settings)
    if not any(isinstance(e, FreeLLMAPISettings) for e in engines):
        yield settings
        return
    job = settings.clone()
    selected = [e for e in (job.translate_engine_settings, job.term_extraction_engine_settings)
                if isinstance(e, FreeLLMAPISettings)]
    for engine in selected:
        engine.validate_settings()
    if any(not engine.freellmapi_base_url for engine in selected):
        raise ValueError("需要已配置的原译翻译接口。")
    yield job
