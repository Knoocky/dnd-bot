import logging
import os
from collections.abc import Iterator

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
_MOJIBAKE_MARKERS = ("Р", "С", "рџ", "вљ", "вЏ", "РІ", "СЂ")


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


def normalize_user_facing_text(text: str) -> str:
    if not text or not any(marker in text for marker in _MOJIBAKE_MARKERS):
        return text

    candidates = [text]
    for source_encoding in ("cp1251", "latin1"):
        try:
            repaired = text.encode(source_encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        candidates.append(repaired)

    def score(value: str) -> tuple[int, int]:
        mojibake_hits = sum(value.count(marker) for marker in _MOJIBAKE_MARKERS)
        readable_hits = sum(
            1
            for char in value
            if ("а" <= char <= "я") or ("А" <= char <= "Я") or char in "ёЁ⚠️🌐⏳🔑"
        )
        return (readable_hits, -mojibake_hits)

    return max(candidates, key=score)


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
        raise RuntimeError(f"Неизвестный AI provider: {raw_provider}. Доступно: {supported}")
    _provider = normalized


def get_provider() -> str:
    global _provider
    if _provider is None:
        configure_provider(None)
    return _provider


def get_model_name() -> str:
    provider = get_provider()
    if provider == "local":
        return _resolve_local_model_name()
    if provider == "claude":
        return os.getenv("ANTHROPIC_MODEL", DEFAULT_MODELS["claude"])
    return os.getenv("OPENAI_MODEL", DEFAULT_MODELS["gpt"])


def supports_streaming() -> bool:
    return get_provider() == "local"


def validate_configuration():
    provider = get_provider()
    if provider == "local":
        _resolve_local_model_name()
        return
    if provider == "claude" and not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("Для провайдера Claude нужен ANTHROPIC_API_KEY в .env")
    if provider == "gpt" and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Для провайдера GPT нужен OPENAI_API_KEY в .env")


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic_client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client


def _get_local_openai_client():
    global _local_openai_client
    if _local_openai_client is None:
        _local_openai_client = OpenAI(
            base_url=_get_local_base_url(),
            api_key=_get_local_api_key(),
        )
    return _local_openai_client


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


def _chat_message_content_to_text(content) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""

    chunks = []
    for item in content:
        if isinstance(item, str):
            if item:
                chunks.append(item)
            continue
        if isinstance(item, dict):
            if item.get("text"):
                chunks.append(str(item["text"]))
            continue
        text = getattr(item, "text", None)
        if text:
            chunks.append(str(text))
    return "".join(chunks)


def _extract_local_chat_output(response) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise RuntimeError("Локальная модель llama.cpp вернула ответ без choices.")

    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    text = _chat_message_content_to_text(content).strip()
    if text:
        return text

    raise RuntimeError("Локальная модель llama.cpp вернула ответ без текстового сообщения.")


def _extract_model_id(item) -> str | None:
    if isinstance(item, dict):
        for key in ("id", "model", "name"):
            value = item.get(key)
            if value:
                return str(value)
        return None

    for attr in ("id", "model", "name"):
        value = getattr(item, attr, None)
        if value:
            return str(value)
    return None


def _extract_first_local_model_id(models_response) -> str:
    data = getattr(models_response, "data", None)
    if data is None and isinstance(models_response, dict):
        data = models_response.get("data")
    if not data and isinstance(models_response, dict):
        data = models_response.get("models")

    if not data:
        raise RuntimeError("Локальный сервер llama.cpp не вернул ни одной модели.")

    model_id = _extract_model_id(data[0])
    if not model_id:
        raise RuntimeError("Локальный сервер llama.cpp вернул модель без идентификатора.")
    return model_id


def _resolve_local_model_name() -> str:
    global _local_model_name

    configured_model = (os.getenv("LLAMA_CPP_MODEL") or "").strip()
    if configured_model:
        return configured_model

    if _local_model_name:
        return _local_model_name

    base_url = _get_local_base_url()
    try:
        models_response = _get_local_openai_client().models.list()
        _local_model_name = _extract_first_local_model_id(models_response)
        logger.info("Resolved local llama.cpp model from %s: %s", base_url, _local_model_name)
        return _local_model_name
    except (openai.APIConnectionError, openai.APITimeoutError):
        logger.warning("Local llama.cpp connection error. base_url=%s", base_url)
        raise RuntimeError(
            f"🌐 **Нет соединения с локальной моделью llama.cpp.** Проверь сервер на `{base_url}`."
        ) from None
    except openai.APIStatusError as error:
        details = _openai_status_error_details(error)
        if error.status_code == 503 and "loading model" in details.lower():
            raise RuntimeError("⏳ **Локальная модель llama.cpp ещё загружается.** Подожди немного и попробуй снова.") from None
        logger.warning(
            "Local llama.cpp model list error. status=%s request_id=%s details=%s",
            error.status_code,
            _exception_request_id(error) or "unknown",
            details,
        )
        raise RuntimeError(f"⚠️ **Ошибка локального сервера llama.cpp ({error.status_code})**: {details}") from None
    except RuntimeError:
        raise
    except Exception as error:
        logger.exception("Unexpected local llama.cpp model resolution error")
        raise RuntimeError("⚠️ **Не удалось получить список моделей llama.cpp.** Проверь локальный сервер.") from error


def _build_local_chat_messages(system_prompt: str, messages: list) -> list[dict]:
    local_messages = [{"role": "system", "content": system_prompt}]
    for message in messages:
        local_messages.append(
            {
                "role": message.get("role", "user"),
                "content": message.get("content", ""),
            }
        )
    return local_messages


def _build_chat_completion_messages(system_blocks: list[str], messages: list) -> list[dict]:
    chat_messages = []
    for block in system_blocks:
        content = str(block or "").strip()
        if not content:
            continue
        chat_messages.append({"role": "system", "content": content})

    for message in messages:
        chat_messages.append(
            {
                "role": message.get("role", "user"),
                "content": message.get("content", ""),
            }
        )
    return chat_messages


def _build_local_request_kwargs(system_prompt: str, messages: list, max_tokens: int) -> dict:
    return {
        "model": get_model_name(),
        "messages": _build_local_chat_messages(system_prompt, messages),
        "max_tokens": max_tokens,
        "extra_body": {"cache_prompt": True},
    }


def _build_local_bootstrap_request_kwargs(system_blocks: list[str], messages: list, max_tokens: int) -> dict:
    return {
        "model": get_model_name(),
        "messages": _build_chat_completion_messages(system_blocks, messages),
        "max_tokens": max_tokens,
        "extra_body": {"cache_prompt": True},
    }


def _build_openai_chat_request_kwargs(
    system_blocks: list[str],
    messages: list,
    max_tokens: int,
    openai_options: dict | None,
) -> dict:
    request_kwargs = {
        "model": get_model_name(),
        "messages": _build_chat_completion_messages(system_blocks, messages),
        "max_completion_tokens": max_tokens,
    }
    if openai_options:
        request_kwargs.update(openai_options)
    return request_kwargs


def _extract_local_stream_delta(event) -> str:
    choices = getattr(event, "choices", None) or []
    if not choices:
        return ""

    choice = choices[0]
    delta = getattr(choice, "delta", None)
    if delta is None and isinstance(choice, dict):
        delta = choice.get("delta")
    if delta is None:
        return ""

    content = getattr(delta, "content", None)
    if content is None and isinstance(delta, dict):
        content = delta.get("content")
    return _chat_message_content_to_text(content)


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
        raise RuntimeError("💸 **Лимиты Claude исчерпаны.** Проверь баланс и квоты в Anthropic.") from None
    except AnthropicStatusError as error:
        error_text = str(error).lower()
        if error.status_code == 401:
            raise RuntimeError("🔑 **Неверный API-ключ Claude.** Проверь `ANTHROPIC_API_KEY`.") from None
        if error.status_code == 529:
            raise RuntimeError("⏳ **Серверы Anthropic перегружены.** Попробуй ещё раз чуть позже.") from None
        if error.status_code == 400 and "credit balance is too low" in error_text:
            raise RuntimeError("💸 **У Anthropic закончился баланс.** Проверь биллинг в консоли.") from None
        raise RuntimeError(f"⚠️ **Ошибка Claude API ({error.status_code})**: {error.message}") from None
    except AnthropicConnectionError:
        raise RuntimeError("🌐 **Нет соединения с Anthropic.** Проверь интернет-подключение.") from None
    except Exception as error:
        logger.exception("Unexpected Claude SDK error")
        raise RuntimeError("⚠️ **Неожиданная ошибка Claude API.** Попробуй ещё раз чуть позже.") from error


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
        raise RuntimeError("💸 **Лимиты GPT/OpenAI исчерпаны.** Проверь квоты и биллинг в OpenAI.") from None
    except openai.AuthenticationError:
        raise RuntimeError("🔑 **Неверный API-ключ OpenAI.** Проверь `OPENAI_API_KEY`.") from None
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

        if error.status_code == 400 and ("insufficient_quota" in error_text or "billing" in error_text):
            logger.warning(
                "OpenAI quota or billing error. status=%s request_id=%s details=%s",
                error.status_code,
                request_id,
                details,
            )
            raise RuntimeError("💸 **Недостаточно квоты OpenAI.** Проверь биллинг и лимиты.") from None

        if error.status_code >= 500:
            logger.warning(
                "OpenAI server error. status=%s request_id=%s details=%s",
                error.status_code,
                request_id,
                details,
            )
            raise RuntimeError("⏳ **Сервер OpenAI временно недоступен.** Попробуй позже.") from None

        logger.warning(
            "OpenAI API status error. status=%s request_id=%s details=%s",
            error.status_code,
            request_id,
            details,
        )
        raise RuntimeError(f"⚠️ **Ошибка OpenAI API ({error.status_code})**: {details}") from None
    except (openai.APIConnectionError, openai.APITimeoutError):
        logger.warning("OpenAI connection or timeout error")
        raise RuntimeError("🌐 **Нет соединения с OpenAI.** Проверь интернет-подключение.") from None
    except RuntimeError:
        raise
    except Exception as error:
        logger.exception("Unexpected OpenAI SDK error")
        raise RuntimeError("⚠️ **Неожиданная ошибка OpenAI API.** Попробуй ещё раз чуть позже.") from error


def _call_local_api(system_prompt: str, messages: list, max_tokens: int = 1000) -> str:
    base_url = _get_local_base_url()
    request_kwargs = _build_local_request_kwargs(system_prompt, messages, max_tokens)

    try:
        logger.debug(
            "Local llama.cpp request started. base_url=%s model=%s messages=%s max_tokens=%s",
            base_url,
            request_kwargs["model"],
            len(request_kwargs["messages"]),
            max_tokens,
        )
        response = _get_local_openai_client().chat.completions.create(**request_kwargs)
        logger.debug(
            "Local llama.cpp request completed. base_url=%s model=%s request_id=%s",
            base_url,
            request_kwargs["model"],
            _response_request_id(response) or "unknown",
        )
        return _extract_local_chat_output(response)
    except openai.AuthenticationError:
        raise RuntimeError("🔑 **Локальный сервер llama.cpp отклонил ключ.** Проверь `LLAMA_CPP_API_KEY`.") from None
    except openai.APIStatusError as error:
        details = _openai_status_error_details(error)
        if error.status_code == 503 and "loading model" in details.lower():
            raise RuntimeError("⏳ **Локальная модель llama.cpp ещё загружается.** Подожди немного и попробуй снова.") from None
        logger.warning(
            "Local llama.cpp API status error. status=%s request_id=%s details=%s",
            error.status_code,
            _exception_request_id(error) or "unknown",
            details,
        )
        raise RuntimeError(f"⚠️ **Ошибка локального сервера llama.cpp ({error.status_code})**: {details}") from None
    except (openai.APIConnectionError, openai.APITimeoutError):
        logger.warning("Local llama.cpp connection or timeout error. base_url=%s", base_url)
        raise RuntimeError(f"🌐 **Нет соединения с локальной моделью llama.cpp.** Проверь сервер на `{base_url}`.") from None
    except RuntimeError:
        raise
    except Exception as error:
        logger.exception("Unexpected local llama.cpp SDK error")
        raise RuntimeError("⚠️ **Неожиданная ошибка локальной модели llama.cpp.** Попробуй ещё раз чуть позже.") from error


def _stream_local_api(system_prompt: str, messages: list, max_tokens: int = 1000) -> Iterator[str]:
    base_url = _get_local_base_url()
    request_kwargs = _build_local_request_kwargs(system_prompt, messages, max_tokens)
    request_kwargs["stream"] = True

    try:
        logger.debug(
            "Local llama.cpp stream started. base_url=%s model=%s messages=%s max_tokens=%s",
            base_url,
            request_kwargs["model"],
            len(request_kwargs["messages"]),
            max_tokens,
        )
        stream = _get_local_openai_client().chat.completions.create(**request_kwargs)
        saw_text = False
        for event in stream:
            chunk = _extract_local_stream_delta(event)
            if not chunk:
                continue
            saw_text = True
            yield chunk
        logger.debug(
            "Local llama.cpp stream completed. base_url=%s model=%s",
            base_url,
            request_kwargs["model"],
        )
        if not saw_text:
            raise RuntimeError("Локальная модель llama.cpp вернула поток без текстового сообщения.")
    except openai.AuthenticationError:
        raise RuntimeError("🔑 **Локальный сервер llama.cpp отклонил ключ.** Проверь `LLAMA_CPP_API_KEY`.") from None
    except openai.APIStatusError as error:
        details = _openai_status_error_details(error)
        if error.status_code == 503 and "loading model" in details.lower():
            raise RuntimeError("⏳ **Локальная модель llama.cpp ещё загружается.** Подожди немного и попробуй снова.") from None
        logger.warning(
            "Local llama.cpp stream API status error. status=%s request_id=%s details=%s",
            error.status_code,
            _exception_request_id(error) or "unknown",
            details,
        )
        raise RuntimeError(f"⚠️ **Ошибка локального сервера llama.cpp ({error.status_code})**: {details}") from None
    except (openai.APIConnectionError, openai.APITimeoutError):
        logger.warning("Local llama.cpp stream connection or timeout error. base_url=%s", base_url)
        raise RuntimeError(f"🌐 **Нет соединения с локальной моделью llama.cpp.** Проверь сервер на `{base_url}`.") from None
    except RuntimeError:
        raise
    except Exception as error:
        logger.exception("Unexpected local llama.cpp stream SDK error")
        raise RuntimeError("⚠️ **Неожиданная ошибка локальной модели llama.cpp.** Попробуй ещё раз чуть позже.") from error


def _call_local_bootstrap_api(system_blocks: list[str], messages: list, max_tokens: int = 1000) -> str:
    base_url = _get_local_base_url()
    request_kwargs = _build_local_bootstrap_request_kwargs(system_blocks, messages, max_tokens)

    try:
        logger.debug(
            "Local llama.cpp bootstrap request started. base_url=%s model=%s messages=%s max_tokens=%s",
            base_url,
            request_kwargs["model"],
            len(request_kwargs["messages"]),
            max_tokens,
        )
        response = _get_local_openai_client().chat.completions.create(**request_kwargs)
        logger.debug(
            "Local llama.cpp bootstrap request completed. base_url=%s model=%s request_id=%s",
            base_url,
            request_kwargs["model"],
            _response_request_id(response) or "unknown",
        )
        return _extract_local_chat_output(response)
    except openai.AuthenticationError:
        raise RuntimeError("🔑 **Локальный сервер llama.cpp отклонил ключ.** Проверь `LLAMA_CPP_API_KEY`.") from None
    except openai.APIStatusError as error:
        details = _openai_status_error_details(error)
        if error.status_code == 503 and "loading model" in details.lower():
            raise RuntimeError("⏳ **Локальная модель llama.cpp ещё загружается.** Подожди немного и попробуй снова.") from None
        logger.warning(
            "Local llama.cpp bootstrap API status error. status=%s request_id=%s details=%s",
            error.status_code,
            _exception_request_id(error) or "unknown",
            details,
        )
        raise RuntimeError(f"⚠️ **Ошибка локального сервера llama.cpp ({error.status_code})**: {details}") from None
    except (openai.APIConnectionError, openai.APITimeoutError):
        logger.warning("Local llama.cpp bootstrap connection or timeout error. base_url=%s", base_url)
        raise RuntimeError(f"🌐 **Нет соединения с локальной моделью llama.cpp.** Проверь сервер на `{base_url}`.") from None
    except RuntimeError:
        raise
    except Exception as error:
        logger.exception("Unexpected local llama.cpp bootstrap SDK error")
        raise RuntimeError("⚠️ **Неожиданная ошибка локальной модели llama.cpp.** Попробуй ещё раз чуть позже.") from error


def _call_openai_bootstrap_api(
    system_blocks: list[str],
    messages: list,
    max_tokens: int = 1000,
    openai_options: dict | None = None,
) -> str:
    request_kwargs = _build_openai_chat_request_kwargs(system_blocks, messages, max_tokens, openai_options)

    try:
        logger.debug(
            "OpenAI bootstrap request started. model=%s messages=%s max_tokens=%s",
            get_model_name(),
            len(request_kwargs["messages"]),
            max_tokens,
        )
        response = _get_openai_client().chat.completions.create(**request_kwargs)
        logger.debug(
            "OpenAI bootstrap request completed. model=%s request_id=%s",
            get_model_name(),
            _response_request_id(response) or "unknown",
        )
        return _extract_local_chat_output(response)
    except openai.RateLimitError:
        raise RuntimeError("💸 **Лимиты GPT/OpenAI исчерпаны.** Проверь квоты и биллинг в OpenAI.") from None
    except openai.AuthenticationError:
        raise RuntimeError("🔑 **Неверный API-ключ OpenAI.** Проверь `OPENAI_API_KEY`.") from None
    except openai.APIStatusError as error:
        request_id = _exception_request_id(error) or "unknown"
        details = _openai_status_error_details(error)
        logger.warning(
            "OpenAI bootstrap API status error. status=%s request_id=%s details=%s",
            error.status_code,
            request_id,
            details,
        )
        raise RuntimeError(f"⚠️ **Ошибка OpenAI API ({error.status_code})**: {details}") from None
    except (openai.APIConnectionError, openai.APITimeoutError):
        logger.warning("OpenAI bootstrap connection or timeout error")
        raise RuntimeError("🌐 **Нет соединения с OpenAI.** Проверь интернет-подключение.") from None
    except RuntimeError:
        raise
    except Exception as error:
        logger.exception("Unexpected OpenAI bootstrap SDK error")
        raise RuntimeError("⚠️ **Неожиданная ошибка OpenAI API.** Попробуй ещё раз чуть позже.") from error


def _call_claude_bootstrap_api(system_blocks: list[str], messages: list, max_tokens: int = 1000) -> str:
    joined_system = "\n\n---\n\n".join(block.strip() for block in system_blocks if str(block or "").strip())
    try:
        logger.debug(
            "Claude bootstrap request started. model=%s messages=%s max_tokens=%s",
            get_model_name(),
            len(messages),
            max_tokens,
        )
        response = _get_anthropic_client().messages.create(
            model=get_model_name(),
            max_tokens=max_tokens,
            system=joined_system,
            messages=messages,
        )
        logger.debug("Claude bootstrap request completed. model=%s", get_model_name())
        return response.content[0].text
    except AnthropicRateLimitError:
        raise RuntimeError("💸 **Лимиты Claude исчерпаны.** Проверь баланс и квоты в Anthropic.") from None
    except AnthropicStatusError as error:
        error_text = str(error).lower()
        if error.status_code == 401:
            raise RuntimeError("🔑 **Неверный API-ключ Claude.** Проверь `ANTHROPIC_API_KEY`.") from None
        if error.status_code == 529:
            raise RuntimeError("⏳ **Серверы Anthropic перегружены.** Попробуй ещё раз чуть позже.") from None
        if error.status_code == 400 and "credit balance is too low" in error_text:
            raise RuntimeError("💸 **У Anthropic закончился баланс.** Проверь биллинг в консоли.") from None
        raise RuntimeError(f"⚠️ **Ошибка Claude API ({error.status_code})**: {error.message}") from None
    except AnthropicConnectionError:
        raise RuntimeError("🌐 **Нет соединения с Anthropic.** Проверь интернет-подключение.") from None
    except Exception as error:
        logger.exception("Unexpected Claude bootstrap SDK error")
        raise RuntimeError("⚠️ **Неожиданная ошибка Claude API.** Попробуй ещё раз чуть позже.") from error


def call_api(
    system_prompt: str,
    messages: list,
    max_tokens: int = 1000,
    openai_options: dict | None = None,
) -> str:
    validate_configuration()
    provider = get_provider()
    if provider == "local":
        return _call_local_api(system_prompt, messages, max_tokens=max_tokens)
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


def call_bootstrap_api(
    system_blocks: list[str],
    messages: list,
    max_tokens: int = 1000,
    openai_options: dict | None = None,
) -> str:
    validate_configuration()
    provider = get_provider()
    if provider == "local":
        return _call_local_bootstrap_api(system_blocks, messages, max_tokens=max_tokens)
    if provider == "claude":
        return _call_claude_bootstrap_api(system_blocks, messages, max_tokens=max_tokens)
    if provider == "gpt":
        return _call_openai_bootstrap_api(
            system_blocks,
            messages,
            max_tokens=max_tokens,
            openai_options=openai_options,
        )
    raise RuntimeError(f"Провайдер {provider} не поддерживается.")


def stream_api(system_prompt: str, messages: list, max_tokens: int = 1000) -> Iterator[str]:
    validate_configuration()
    provider = get_provider()
    if provider != "local":
        raise RuntimeError(f"Потоковый вывод не поддерживается для провайдера {provider}.")
    yield from _stream_local_api(system_prompt, messages, max_tokens=max_tokens)
