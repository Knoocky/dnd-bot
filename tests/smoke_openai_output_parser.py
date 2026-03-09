from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_provider import _extract_openai_output


def _response(output_text=None, output=None, request_id="req_test"):
    return SimpleNamespace(output_text=output_text, output=output or [], request_id=request_id)


def _message(*content_items):
    return SimpleNamespace(type="message", content=list(content_items))


def _reasoning():
    return SimpleNamespace(type="reasoning", content=None)


def _output_text(text):
    return SimpleNamespace(type="output_text", text=text)


def _refusal(text):
    return SimpleNamespace(type="refusal", refusal=text)


def main():
    cases = [
        ("direct output_text", _response(output_text="ok"), "ok"),
        (
            "message fallback",
            _response(output_text="", output=[_message(_output_text("from-message"))]),
            "from-message",
        ),
        (
            "reasoning then message",
            _response(
                output_text="",
                output=[_reasoning(), _message(_output_text("after-reasoning"))],
            ),
            "after-reasoning",
        ),
        (
            "refusal fallback",
            _response(output_text="", output=[_message(_refusal("refused"))]),
            "refused",
        ),
    ]

    for label, response, expected in cases:
        actual = _extract_openai_output(response)
        if actual != expected:
            raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")

    print("ok")


if __name__ == "__main__":
    main()
