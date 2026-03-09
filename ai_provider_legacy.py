import logging
import os

import anthropic
import openai
from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import APIStatusError as AnthropicStatusError
from anthropic import RateLimitError as AnthropicRateLimitError
from openai import OpenAI

logger = logging.getLogger("dnd_bot.ai")

LLAMA_CPP_DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"

PROVIDER_ALIASES = {
    "local": "local",
    "llama": "local",
    "llama.cpp": "local",
    "llamacpp": "local",
    "claude": "claude",
    "anthropic": "claude",
    "gpt": "gpt",
    "openai": "gpt",
}

DEFAULT_PROVIDER = "local"
DEFAULT_MODELS = {
    "local": "autodetect",
    "claude": "claude-sonnet-4-20250514",
    "gpt": "gpt-5.2",
}

_provider = None
_anthropic_client = None
_openai_client = None
_local_openai_client = None
_local_model_name = None


def _response_request_id(response) -> str | None:
    return getattr(response, "_request_id", None) or getattr(response, "request_id", None)


def _exception_request_id(error) -> str | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        return headers.get("x-request-id")
    return getattr(error, "request_id", None)


def _response_status(response) -> str | None:
    return getattr(response, "status", None)


def _response_incomplete_reason(response) -> str | None:
    incomplete_details = getattr(response, "incomplete_details", None)
    return getattr(incomplete_details, "reason", None)


def _openai_status_error_details(error) -> str:
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        nested = body.get("error")
        if isinstance(nested, dict):
            message = nested.get("message")
            if message:
                return str(message)
        message = body.get("message")
        if message:
            return str(message)
    message = getattr(error, "message", None)
    if message:
        return str(message)
    return str(error)


def _get_local_base_url() -> str:
    return (os.getenv("LLAMA_CPP_BASE_URL") or LLAMA_CPP_DEFAULT_BASE_URL).rstrip("/")


def _get_local_api_key() -> str:
    return os.getenv("LLAMA_CPP_API_KEY") or "local"


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


def _openai_output_type_summary(response) -> list[str]:
    summary = []
    for item in getattr(response, "output", []) or []:
        summary.append(str(getattr(item, "type", type(item).__name__)))
    return summary


def _log_openai_response(response):
    logger.debug(
        "OpenAI request completed. model=%s request_id=%s status=%s incomplete_reason=%s output_types=%s has_output_text=%s",
        get_model_name(),
        _response_request_id(response) or "unknown",
        _response_status(response) or "unknown",
        _response_incomplete_reason(response) or "none",
        _openai_output_type_summary(response),
        bool(getattr(response, "output_text", None)),
    )


def _should_retry_openai_response(response) -> bool:
    if _response_incomplete_reason(response) == "max_output_tokens":
        return True
    if getattr(response, "output_text", None):
        return False
    return _openai_output_type_summary(response) == ["reasoning"]


def _extract_openai_output(response) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text

    chunks = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", "") != "message":
            continue
        for content in getattr(item, "content", None) or []:
            content_type = getattr(content, "type", "")
            if content_type in ("output_text", "text"):
                value = getattr(content, "text", None)
                if value:
                    chunks.append(value)
            elif content_type == "refusal":
                refusal = getattr(content, "refusal", None)
                if refusal:
                    chunks.append(refusal)

    if chunks:
        return "".join(chunks)

    request_id = _response_request_id(response) or "unknown"
    logger.warning(
        "OpenAI response had no assistant text output. request_id=%s status=%s incomplete_reason=%s output_types=%s has_output_text=%s",
        request_id,
        _response_status(response) or "unknown",
        _response_incomplete_reason(response) or "none",
        _openai_output_type_summary(response),
        bool(getattr(response, "output_text", None)),
    )
    raise RuntimeError("GPT API вернул ответ без текстового сообщения.")


def _build_openai_request_kwargs(
    system_prompt: str,
    messages: list,
    max_tokens: int,
    openai_options: dict | None,
) -> dict:
    request_kwargs = {
        "model": get_model_name(),
        "instructions": system_prompt,
        "input": messages,
        "max_output_tokens": max_tokens,
    }
    if openai_options:
        request_kwargs.update(openai_options)
    return request_kwargs


def _send_openai_request(request_kwargs: dict):
    response = _get_openai_client().responses.create(**request_kwargs)
    _log_openai_response(response)
    return response


def _extract_openai_output_with_retry(response, request_kwargs: dict) -> str:
    should_retry = _should_retry_openai_response(response)
    extracted = None
    try:
        extracted = _extract_openai_output(response)
    except RuntimeError:
        if not should_retry:
            raise

    if not should_retry:
        return extracted

    old_max_tokens = int(request_kwargs.get("max_output_tokens") or 1000)
    retry_kwargs = dict(request_kwargs)
    retry_kwargs["max_output_tokens"] = min(max(old_max_tokens * 2, old_max_tokens + 800), 4000)

    retry_reasoning = dict(retry_kwargs.get("reasoning") or {})
    retry_reasoning["effort"] = "minimal"
    retry_kwargs["reasoning"] = retry_reasoning

    logger.warning(
        "Retrying OpenAI request after incomplete response. request_id=%s status=%s incomplete_reason=%s old_max_output_tokens=%s new_max_output_tokens=%s output_types=%s had_output_text=%s",
        _response_request_id(response) or "unknown",
        _response_status(response) or "unknown",
        _response_incomplete_reason(response) or "none",
        old_max_tokens,
        retry_kwargs["max_output_tokens"],
        _openai_output_type_summary(response),
        bool(getattr(response, "output_text", None)),
    )
    retry_response = _send_openai_request(retry_kwargs)
    return _extract_openai_output(retry_response)


def _call_claude_api(system_prompt: str, messages: list, max_tokens: int = 1000) -> str:
    try:
        logger.debug(
            "Claude request started. model=%s messages=%s max_tokens=%s",
            get_model_name(),
            len(messages),
            max_tokens,
        )
        response = _get_anthropic_client().messages.create(
            model=get_model_name(),
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
        )
        logger.debug("Claude request completed. model=%s", get_model_name())
        return response.content[0].text
    except AnthropicRateLimitError:
        raise RuntimeError(
            "💸 **Лимиты Claude исчерпаны.** Проверь баланс и квоты в Anthropic."
        ) from None
    except AnthropicStatusError as error:
        error_text = str(error).lower()
        if error.status_code == 401:
            raise RuntimeError(
                "🔑 **Неверный API-ключ Claude.** Проверь `ANTHROPIC_API_KEY`."
            ) from None
        if error.status_code == 529:
            raise RuntimeError(
                "⏳ **Серверы Anthropic перегружены.** Попробуй ещё раз чуть позже."
            ) from None
        if error.status_code == 400 and "credit balance is too low" in error_text:
            raise RuntimeError(
                "💸 **У Anthropic закончился баланс.** Проверь биллинг в консоли."
            ) from None
        raise RuntimeError(
            f"⚠️ **Ошибка Claude API ({error.status_code})**: {error.message}"
        ) from None
    except AnthropicConnectionError:
        raise RuntimeError(
            "🌐 **Нет соединения с Anthropic.** Проверь интернет-подключение."
        ) from None
    except Exception as error:
        logger.exception("Unexpected Claude SDK error")
        raise RuntimeError(
            "⚠️ **Неожиданная ошибка Claude API.** Попробуй ещё раз чуть позже."
        ) from error


def _call_openai_api(
    system_prompt: str,
    messages: list,
    max_tokens: int = 1000,
    openai_options: dict | None = None,
) -> str:
    request_kwargs = _build_openai_request_kwargs(
        system_prompt,
        messages,
        max_tokens,
        openai_options,
    )
    try:
        logger.debug(
            "OpenAI request started. model=%s messages=%s max_tokens=%s",
            get_model_name(),
            len(messages),
            max_tokens,
        )
        response = _send_openai_request(request_kwargs)
        return _extract_openai_output_with_retry(response, request_kwargs)
    except openai.RateLimitError:
        raise RuntimeError(
            "💸 **Лимиты GPT/OpenAI исчерпаны.** Проверь квоты и биллинг в OpenAI."
        ) from None
    except openai.AuthenticationError:
        raise RuntimeError(
            "🔑 **Неверный API-ключ OpenAI.** Проверь `OPENAI_API_KEY`."
        ) from None
    except openai.APIStatusError as error:
        request_id = _exception_request_id(error) or "unknown"
        details = _openai_status_error_details(error)
        error_text = details.lower()

        if error.status_code == 400 and openai_options:
            logger.warning(
                "OpenAI rejected optional request tuning. request_id=%s details=%s Retrying without optional options.",
                request_id,
                details,
            )
            fallback_kwargs = _build_openai_request_kwargs(
                system_prompt,
                messages,
                max_tokens,
                None,
            )
            response = _send_openai_request(fallback_kwargs)
            return _extract_openai_output_with_retry(response, fallback_kwargs)

        if error.status_code == 400 and (
            "insufficient_quota" in error_text or "billing" in error_text
        ):
            logger.warning(
                "OpenAI quota or billing error. status=%s request_id=%s details=%s",
                error.status_code,
                request_id,
                details,
            )
            raise RuntimeError(
                "💸 **Недостаточно квоты OpenAI.** Проверь биллинг и лимиты."
            ) from None

        if error.status_code >= 500:
            logger.warning(
                "OpenAI server error. status=%s request_id=%s details=%s",
                error.status_code,
                request_id,
                details,
            )
            raise RuntimeError(
                "⏳ **Сервер OpenAI временно недоступен.** Попробуй позже."
            ) from None

        logger.warning(
            "OpenAI API status error. status=%s request_id=%s details=%s",
            error.status_code,
            request_id,
            details,
        )
        raise RuntimeError(
            f"⚠️ **Ошибка OpenAI API ({error.status_code})**: {details}"
        ) from None
    except (openai.APIConnectionError, openai.APITimeoutError):
        logger.warning("OpenAI connection or timeout error")
        raise RuntimeError(
            "🌐 **Нет соединения с OpenAI.** Проверь интернет-подключение."
        ) from None
    except RuntimeError:
        raise
    except Exception as error:
        logger.exception("Unexpected OpenAI SDK error")
        raise RuntimeError(
            "⚠️ **Неожиданная ошибка OpenAI API.** Попробуй ещё раз чуть позже."
        ) from error


def call_api(
    system_prompt: str,
    messages: list,
    max_tokens: int = 1000,
    openai_options: dict | None = None,
) -> str:
    validate_configuration()
    provider = get_provider()
    if provider == "claude":
        return _call_claude_api(system_prompt, messages, max_tokens=max_tokens)
    if provider == "gpt":
        return _call_openai_api(
            system_prompt,
            messages,
            max_tokens=max_tokens,
            openai_options=openai_options,
        )
    raise RuntimeError(f"Провайдер {provider} не поддерживается.")
