import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ai_provider_runtime as ai_provider
import dungeon_master


def _chat_response(content):
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice], request_id="chat_test")


def _models_response(*items):
    return SimpleNamespace(data=list(items))


def main():
    if ai_provider._extract_local_chat_output(_chat_response("ok")) != "ok":
        raise AssertionError("local chat output should return plain string content")

    rich_content = [SimpleNamespace(text="from"), SimpleNamespace(text="-parts")]
    if ai_provider._extract_local_chat_output(_chat_response(rich_content)) != "from-parts":
        raise AssertionError("local chat output should join structured content parts")

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
        if not dungeon_master.get_system_prompt(0).startswith(dungeon_master.OPENAI_SYSTEM_PROMPT):
            raise AssertionError("local provider should use OpenAI-compatible system prompt")
    finally:
        ai_provider._provider = previous_provider
        if previous_env is None:
            os.environ.pop("AI_PROVIDER", None)
        else:
            os.environ["AI_PROVIDER"] = previous_env

    print("ok")


if __name__ == "__main__":
    main()
