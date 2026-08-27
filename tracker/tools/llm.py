"""LLM factory.

We use OpenRouter's OpenAI-compatible chat completions endpoint, targeting
``google/gemini-2.5-pro`` and authenticating with ``OPENROUTER_API_KEY``.
LangGraph nodes call :func:`get_chat_model` to obtain a ready-to-use LangChain
chat model.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from tracker.config import Settings, load_settings


def get_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """Return a configured chat model backed by OpenRouter / gemini-2.5-pro.

    OpenRouter is OpenAI-compatible, so ``ChatOpenAI`` works against it once
    pointed at the right ``base_url`` and given ``OPENROUTER_API_KEY`` as the
    key. The ``HTTP-Referer`` / ``X-OpenRouter-Title`` headers are optional
    attribution for OpenRouter's leaderboards.
    """

    settings = settings or load_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.require_openrouter_key(),
        temperature=settings.llm_temperature,
        default_headers={
            "HTTP-Referer": "https://github.com/canonical/mx-upstream-tracker",
            "X-OpenRouter-Title": "mx-upstream-tracker",
        },
    )
