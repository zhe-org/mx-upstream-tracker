"""LLM factory.

We use GitHub Copilot's OpenAI-compatible chat completions endpoint, targeting
``gemini-2.5-pro`` and authenticating with ``GH_TOKEN``. LangGraph nodes call
:func:`get_chat_model` to obtain a ready-to-use LangChain chat model.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from config import Settings, load_settings


def get_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    """Return a configured chat model backed by Copilot / gemini-2.5-pro.

    The Copilot API is OpenAI-compatible, so ``ChatOpenAI`` works against it
    once pointed at the right ``base_url`` and given ``GH_TOKEN`` as the key.
    """

    settings = settings or load_settings()
    return ChatOpenAI(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.require_gh_token(),
        temperature=settings.llm_temperature,
        default_headers={
            # Copilot requires an editor/integration identifier on requests.
            "Editor-Version": "k8s-upstream-tracker/0.1.0",
            "Copilot-Integration-Id": "vscode-chat",
        },
    )
