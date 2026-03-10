import re
from pathlib import Path


ADVENTURES_DIR = Path(__file__).resolve().parent / "data" / "adventures"
DESCRIPTION_MAX_LENGTH = 180
LOCAL_SOURCE_CHAR_LIMIT = 9000
LOCAL_SECTION_CHAR_LIMIT = 650
LOCAL_SECTION_COUNT_LIMIT = 8
LOCAL_OUTLINE_COUNT_LIMIT = 18
GENERIC_TITLES = {
    "a book of books",
    "about the adventure",
    "adventure overview",
    "background",
    "chapter 1",
    "chapter 2",
    "chapter 3",
    "chapter 4",
    "chapter 5",
    "dungeon mastering the adventure",
    "introduction",
    "introducing characters",
    "introducing the game",
    "overview",
    "plot",
    "preparation",
    "summary",
    "using the adventures",
}
TITLE_IN_PROSE_RE = re.compile(
    r"\*{1,2}(?P<title>[^*\n]{3,120}?)\*{1,2}\s+(?:is|was|begins|contains|presents|introduces)\b",
    re.IGNORECASE,
)
INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
EMPHASIS_RE = re.compile(r"[*_`]+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
SENTENCE_RE = re.compile(r"(.+?[.!?])(?:\s|$)")


def _read_markdown(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _normalize_fallback_title(stem: str) -> str:
    title = re.sub(r"\s*_\s*", ": ", stem.strip())
    title = title.replace("-", " ")
    title = re.sub(r"\s+", " ", title).strip(" :")
    if title and title.lower() == title:
        title = title.title()
    return title or "Приключение"


def _strip_markdown(text: str) -> str:
    cleaned = IMAGE_RE.sub(" ", text or "")
    cleaned = INLINE_LINK_RE.sub(lambda match: match.group(1) or match.group(2), cleaned)
    cleaned = re.sub(r"^>+\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = EMPHASIS_RE.sub("", cleaned)
    cleaned = cleaned.replace("—", "-")
    return re.sub(r"\s+", " ", cleaned).strip()


def _normalize_key(text: str) -> str:
    cleaned = _strip_markdown(text).strip(" :.-").lower()
    return re.sub(r"\s+", " ", cleaned)


def _split_paragraphs(lines: list[str]) -> list[str]:
    paragraphs = []
    current = []
    for line in lines:
        if line.strip():
            current.append(line.rstrip())
            continue
        if current:
            paragraphs.append("\n".join(current).strip())
            current = []
    if current:
        paragraphs.append("\n".join(current).strip())
    return paragraphs


def _is_generic_heading(title: str) -> bool:
    key = _normalize_key(title)
    if key in GENERIC_TITLES:
        return True
    return bool(re.fullmatch(r"chapter\s+\d+", key))


def _iter_prelude_lines(lines: list[str]) -> list[str]:
    prelude = []
    for line in lines:
        match = HEADING_RE.match(line.strip())
        if match and len(match.group(1)) >= 2:
            break
        prelude.append(line)
    return prelude


def _extract_title(prelude_lines: list[str], fallback_title: str) -> str:
    for line in prelude_lines:
        match = HEADING_RE.match(line.strip())
        if not match or len(match.group(1)) != 1:
            continue
        heading = _strip_markdown(match.group(2))
        if heading and not _is_generic_heading(heading):
            return heading

    prelude_text = "\n".join(prelude_lines)
    title_match = TITLE_IN_PROSE_RE.search(prelude_text)
    if title_match:
        return _strip_markdown(title_match.group("title"))

    return fallback_title


def _is_safe_description_paragraph(paragraph: str) -> bool:
    stripped = paragraph.strip()
    if not stripped:
        return False
    if stripped.startswith(("#", ">", "-", "|", "![", ">>")):
        return False
    if stripped.startswith("*") and stripped.endswith("*"):
        return False
    cleaned = _strip_markdown(stripped)
    return len(cleaned) >= 12


def _first_sentence(text: str) -> str:
    match = SENTENCE_RE.match(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _truncate(text: str, limit: int = DESCRIPTION_MAX_LENGTH) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _extract_description(prelude_lines: list[str], title: str) -> str:
    for paragraph in _split_paragraphs(prelude_lines):
        if not _is_safe_description_paragraph(paragraph):
            continue
        cleaned = _strip_markdown(paragraph)
        if _normalize_key(cleaned) == _normalize_key(title):
            continue
        sentence = _truncate(_first_sentence(cleaned))
        if sentence:
            return sentence
    return ""


def _extract_title_and_description(text: str, fallback_title: str) -> tuple[str, str]:
    lines = [line.rstrip() for line in text.splitlines()]
    prelude_lines = _iter_prelude_lines(lines)
    title = _extract_title(prelude_lines, fallback_title)
    description = _extract_description(prelude_lines, title)
    return title, description


def _iter_sections(text: str) -> list[dict]:
    sections = []
    current_heading = None
    current_level = None
    current_lines = []

    def flush():
        if current_heading is None and not current_lines:
            return
        sections.append(
            {
                "heading": current_heading,
                "level": current_level,
                "lines": list(current_lines),
            }
        )

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        match = HEADING_RE.match(line.strip())
        if match:
            flush()
            current_heading = _strip_markdown(match.group(2))
            current_level = len(match.group(1))
            current_lines = []
            continue
        current_lines.append(line)

    flush()
    return sections


def _section_summary(lines: list[str], char_limit: int = LOCAL_SECTION_CHAR_LIMIT) -> str:
    parts = []
    for paragraph in _split_paragraphs(lines):
        if not _is_safe_description_paragraph(paragraph):
            continue
        cleaned = _strip_markdown(paragraph)
        if not cleaned:
            continue
        parts.append(cleaned)
        if len(" ".join(parts)) >= char_limit:
            break
    if not parts:
        return ""
    summary = " ".join(parts[:2])
    return _truncate(summary, char_limit)


def build_local_adventure_context(adventure: dict, char_limit: int = LOCAL_SOURCE_CHAR_LIMIT) -> str:
    title = str(adventure.get("title") or "").strip() or _normalize_fallback_title(adventure.get("slug") or "")
    slug = str(adventure.get("slug") or "").strip() or "adventure"
    content = str(adventure.get("content") or "")
    description = str(adventure.get("description") or "").strip()
    sections = _iter_sections(content)

    lines = [
        "[КОМПАКТНАЯ СВОДКА ИСТОЧНИКА ДЛЯ ЛОКАЛЬНОЙ МОДЕЛИ]",
        f"slug: {slug}",
        f"title: {title}",
    ]
    if description:
        lines.append(f"summary: {description}")

    outline = []
    for section in sections:
        heading = section.get("heading")
        level = section.get("level")
        if not heading or level is None or level > 2:
            continue
        outline.append(f"{'  ' * max(0, level - 1)}- {heading}")
        if len(outline) >= LOCAL_OUTLINE_COUNT_LIMIT:
            break
    if outline:
        lines.extend(["", "Структура источника:", *outline])

    added_sections = 0
    for section in sections:
        heading = section.get("heading")
        if not heading:
            continue
        summary = _section_summary(section.get("lines") or [])
        if not summary:
            continue
        candidate = "\n".join([*lines, "", f"## {heading}", summary])
        if len(candidate) > char_limit:
            break
        lines.extend(["", f"## {heading}", summary])
        added_sections += 1
        if added_sections >= LOCAL_SECTION_COUNT_LIMIT:
            break

    return _truncate("\n".join(lines), char_limit)


def _tokenize_query(text: str) -> set[str]:
    normalized = _normalize_key(text)
    return {
        token
        for token in re.split(r"[^a-zA-Zа-яА-Я0-9']+", normalized)
        if len(token) >= 4 and not token.isdigit()
    }


def build_relevant_local_adventure_context(
    adventure: dict,
    query_text: str,
    char_limit: int = 2500,
) -> str:
    query_tokens = _tokenize_query(query_text)
    if not query_tokens:
        return ""

    sections = _iter_sections(str(adventure.get("content") or ""))
    ranked = []
    for index, section in enumerate(sections):
        heading = str(section.get("heading") or "").strip()
        lines = section.get("lines") or []
        summary = _section_summary(lines, char_limit=min(LOCAL_SECTION_CHAR_LIMIT, 450))
        if not heading or not summary:
            continue
        haystack = _normalize_key(f"{heading} {summary}")
        score = 0
        for token in query_tokens:
            if token in haystack:
                score += 3 if token in _normalize_key(heading) else 1
        if score <= 0:
            continue
        ranked.append((score, index, heading, summary))

    if not ranked:
        return ""

    ranked.sort(key=lambda item: (-item[0], item[1]))
    lines = [
        "[РЕЛЕВАНТНЫЕ ФРАГМЕНТЫ ИСТОЧНИКА]",
        f"title: {adventure.get('title') or adventure.get('slug')}",
    ]
    for _, _, heading, summary in ranked[:4]:
        candidate = "\n".join([*lines, "", f"## {heading}", summary])
        if len(candidate) > char_limit:
            break
        lines.extend(["", f"## {heading}", summary])

    if len(lines) <= 2:
        return ""
    return _truncate("\n".join(lines), char_limit)


def list_adventures() -> list[dict]:
    adventures = []
    if not ADVENTURES_DIR.exists():
        return adventures

    for path in sorted(ADVENTURES_DIR.glob("*.md")):
        content = _read_markdown(path)
        title, description = _extract_title_and_description(content, _normalize_fallback_title(path.stem))
        adventures.append(
            {
                "slug": path.stem,
                "title": title,
                "description": description,
                "path": str(path),
            }
        )
    return adventures


def get_adventure(slug: str) -> dict | None:
    normalized_slug = (slug or "").strip().lower()
    for adventure in list_adventures():
        if adventure["slug"].lower() != normalized_slug:
            continue
        content = _read_markdown(Path(adventure["path"]))
        return {
            **adventure,
            "content": content,
        }
    return None
