import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai_provider_runtime as ai_provider
from cogs.game import _visible_stream_text
import dungeon_master


def _chat_response(content):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], request_id="chat_test")


def _models_response(*items):
    return SimpleNamespace(data=list(items))


def _stream_event(content):
    delta = SimpleNamespace(content=content)
    choice = SimpleNamespace(delta=delta)
    return SimpleNamespace(choices=[choice])


def main():
    previous_local_model = os.environ.get("LLAMA_CPP_MODEL")
    previous_cached_local_model = ai_provider._local_model_name
    try:
        os.environ["LLAMA_CPP_MODEL"] = "test-local-model"
        ai_provider._local_model_name = None
        request_kwargs = ai_provider._build_local_request_kwargs(
            "system here",
            [{"role": "user", "content": "hello"}],
            123,
        )
    finally:
        ai_provider._local_model_name = previous_cached_local_model
        if previous_local_model is None:
            os.environ.pop("LLAMA_CPP_MODEL", None)
        else:
            os.environ["LLAMA_CPP_MODEL"] = previous_local_model

    if request_kwargs["model"] != "test-local-model":
        raise AssertionError("local request kwargs should respect LLAMA_CPP_MODEL without autodetect")
    if request_kwargs.get("extra_body", {}).get("cache_prompt") is not True:
        raise AssertionError("local request kwargs should enable cache_prompt via extra_body")
    if request_kwargs["messages"][0] != {"role": "system", "content": "system here"}:
        raise AssertionError("local request kwargs should send system prompt as system message")

    if ai_provider._extract_local_chat_output(_chat_response("ok")) != "ok":
        raise AssertionError("local chat output should return plain string content")

    rich_content = [SimpleNamespace(text="from"), SimpleNamespace(text="-parts")]
    if ai_provider._extract_local_chat_output(_chat_response(rich_content)) != "from-parts":
        raise AssertionError("local chat output should join structured content parts")

    stream_rich_content = [{"type": "text", "text": "part"}, {"type": "output_text", "text": "-2"}]
    if ai_provider._extract_local_stream_delta(_stream_event(stream_rich_content)) != "part-2":
        raise AssertionError("local stream delta should join structured text parts")

    if ai_provider._extract_local_stream_delta(_stream_event(None)) != "":
        raise AssertionError("empty local stream delta should return empty string")

    try:
        ai_provider._extract_local_chat_output(_chat_response(""))
    except RuntimeError:
        pass
    else:
        raise AssertionError("empty local chat content should raise RuntimeError")

    detected = ai_provider._extract_first_local_model_id(
        _models_response(SimpleNamespace(id="model-a"), SimpleNamespace(id="model-b"))
    )
    if detected != "model-a":
        raise AssertionError(f"expected model-a, got {detected!r}")

    try:
        ai_provider._extract_first_local_model_id(SimpleNamespace(data=[]))
    except RuntimeError:
        pass
    else:
        raise AssertionError("empty local model list should raise RuntimeError")

    previous_provider = ai_provider._provider
    previous_env = os.environ.get("AI_PROVIDER")
    try:
        ai_provider.configure_provider("local")
        if not dungeon_master.get_system_prompt(0).startswith(dungeon_master.LOCAL_SYSTEM_PROMPT):
            raise AssertionError("local provider should use compact local system prompt")
    finally:
        ai_provider._provider = previous_provider
        if previous_env is None:
            os.environ.pop("AI_PROVIDER", None)
        else:
            os.environ["AI_PROVIDER"] = previous_env

    visible = _visible_stream_text("Текст до броска\n[ROLL_REQUEST]\n{\"dice_count\": 1}\n[/ROLL_REQUEST]")
    if visible != "Текст до броска":
        raise AssertionError("stream visibility helper should hide roll request block")

    if _visible_stream_text("Обычный ответ без блока") != "Обычный ответ без блока":
        raise AssertionError("stream visibility helper should keep normal text unchanged")

    print("ok")


if __name__ == "__main__":
    main()
