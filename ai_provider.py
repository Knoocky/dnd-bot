import os

import anthropic
import openai
from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import APIStatusError as AnthropicStatusError
from anthropic import RateLimitError as AnthropicRateLimitError
from openai import OpenAI

PROVIDER_ALIASES = {
    "claude": "claude",
    "anthropic": "claude",
    "gpt": "gpt",
    "openai": "gpt",
}

DEFAULT_PROVIDER = "claude"
DEFAULT_MODELS = {
    "claude": "claude-sonnet-4-20250514",
    "gpt": "gpt-5.2",
}

_provider = None
_anthropic_client = None
_openai_client = None


def configure_provider(provider: str | None):
    global _provider
    raw_provider = provider or os.getenv("AI_PROVIDER", DEFAULT_PROVIDER)
    normalized = PROVIDER_ALIASES.get(raw_provider.strip().lower())
    if not normalized:
        supported = ", ".join(sorted(set(PROVIDER_ALIASES)))
        raise RuntimeError(
            f"Неизвестный AI provider: {raw_provider}. Доступно: {supported}"
        )
    _provider = normalized


def get_provider() -> str:
    global _provider
    if _provider is None:
        configure_provider(None)
    return _provider


def get_model_name() -> str:
    provider = get_provider()
    if provider == "claude":
        return os.getenv("ANTHROPIC_MODEL", DEFAULT_MODELS["claude"])
    return os.getenv("OPENAI_MODEL", DEFAULT_MODELS["gpt"])


def validate_configuration():
    provider = get_provider()
    if provider == "claude" and not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "Для провайдера Claude нужен ANTHROPIC_API_KEY в .env"
        )
    if provider == "gpt" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Для провайдера GPT нужен OPENAI_API_KEY в .env")


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
    return _anthropic_client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client


def _extract_openai_output(response) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text

    chunks = []
    for item in getattr(response, "output", []):
        for content in getattr(item, "content", []):
            content_type = getattr(content, "type", "")
            if content_type in ("output_text", "text"):
                value = getattr(content, "text", None)
                if value:
                    chunks.append(value)

    if chunks:
        return "".join(chunks)

    raise RuntimeError("GPT API вернул пустой ответ.")


def _call_claude_api(system_prompt: str, messages: list, max_tokens: int = 1000) -> str:
    try:
        response = _get_anthropic_client().messages.create(
            model=get_model_name(),
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )
        return response.content[0].text
    except AnthropicRateLimitError:
        raise RuntimeError(
            "💸 **Лимиты Claude исчерпаны.** Проверь баланс и квоты в Anthropic."
        ) from None
    except AnthropicStatusError as e:
        error_text = str(e).lower()
        if e.status_code == 401:
            raise RuntimeError(
                "🔑 **Неверный API-ключ Claude.** Проверь `ANTHROPIC_API_KEY`."
            ) from None
        if e.status_code == 529:
            raise RuntimeError(
                "⏳ **Серверы Anthropic перегружены.** Попробуй ещё раз чуть позже."
            ) from None
        if e.status_code == 400 and "credit balance is too low" in error_text:
            raise RuntimeError(
                "💸 **У Anthropic закончился баланс.** Проверь биллинг в консоли."
            ) from None
        raise RuntimeError(
            f"⚠️ **Ошибка Claude API ({e.status_code})**: {e.message}"
        ) from None
    except AnthropicConnectionError:
        raise RuntimeError(
            "🌐 **Нет соединения с Anthropic.** Проверь интернет-подключение."
        ) from None


def _call_openai_api(system_prompt: str, messages: list, max_tokens: int = 1000) -> str:
    try:
        response = _get_openai_client().responses.create(
            model=get_model_name(),
            instructions=system_prompt,
            input=messages,
            max_output_tokens=max_tokens,
        )
        return _extract_openai_output(response)
    except openai.RateLimitError:
        raise RuntimeError(
            "💸 **Лимиты GPT/OpenAI исчерпаны.** Проверь квоты и биллинг в OpenAI."
        ) from None
    except openai.AuthenticationError:
        raise RuntimeError(
            "🔑 **Неверный API-ключ OpenAI.** Проверь `OPENAI_API_KEY`."
        ) from None
    except openai.APIStatusError as e:
        error_text = str(e).lower()
        if e.status_code == 400 and (
            "insufficient_quota" in error_text or "billing" in error_text
        ):
            raise RuntimeError(
                "💸 **Недостаточно квоты OpenAI.** Проверь биллинг и лимиты."
            ) from None
        if e.status_code >= 500:
            raise RuntimeError(
                "⏳ **Сервер OpenAI временно недоступен.** Попробуй позже."
            ) from None
        raise RuntimeError(
            f"⚠️ **Ошибка OpenAI API ({e.status_code})**: {e.response}"
        ) from None
    except (openai.APIConnectionError, openai.APITimeoutError):
        raise RuntimeError(
            "🌐 **Нет соединения с OpenAI.** Проверь интернет-подключение."
        ) from None


def call_api(system_prompt: str, messages: list, max_tokens: int = 1000) -> str:
    validate_configuration()
    provider = get_provider()
    if provider == "claude":
        return _call_claude_api(system_prompt, messages, max_tokens=max_tokens)
    if provider == "gpt":
        return _call_openai_api(system_prompt, messages, max_tokens=max_tokens)
    raise RuntimeError(f"Провайдер {provider} не поддерживается.")
