from __future__ import annotations

import html
import re
from typing import Any


_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_REF_PAIR_RE = re.compile(r"<ref\b[^>/]*>.*?</ref\s*>", re.DOTALL | re.IGNORECASE)
_REF_SELF_RE = re.compile(r"<ref\b[^>]*/\s*>", re.IGNORECASE)
_TAG_RE = re.compile(r"</?[^>]+>")
_HEADING_RE = re.compile(r"(?m)^(={1,6})\s*(.*?)\s*\1\s*$")
_LIST_RE = re.compile(r"^([*#;:]+)\s*(.*?)\s*$")
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")
_EXTERNAL_LINK_RE = re.compile(r"\[((?:https?|ftp)://[^\s\]]+)(?:\s+([^\]]+))?\]")
_PROTECTED_RE = re.compile(
    r"<!--.*?-->|<(nowiki|pre|source|syntaxhighlight|math)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)


def clean_wikitext(text: str, *, remove_templates: bool = False) -> str:
    """Conservatively turn small pieces of wikitext into readable text.

    The function intentionally does not try to understand wiki semantics.
    References, comments, HTML tags, bold/italic markup and link syntax are
    removed while visible labels are kept. Templates are preserved by default
    because they may contain meaningful data. Set ``remove_templates=True``
    when a plain-text approximation is preferable to preserving them.
    """
    value = str(text)
    value = _COMMENT_RE.sub(" ", value)
    value = _REF_PAIR_RE.sub(" ", value)
    value = _REF_SELF_RE.sub(" ", value)

    def wiki_link(match: re.Match[str]) -> str:
        inner = match.group(1)
        parts = inner.split("|")
        return parts[-1].strip() if parts else inner.strip()

    value = _WIKILINK_RE.sub(wiki_link, value)
    value = _EXTERNAL_LINK_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), value)

    if remove_templates:
        spans = _template_spans(value)
        for span in sorted(spans, key=lambda item: item[0], reverse=True):
            start, end, _depth = span
            value = value[:start] + " " + value[end:]

    value = _TAG_RE.sub(" ", value)
    value = value.replace("'''''", "").replace("'''", "").replace("''", "")
    value = html.unescape(value)
    return " ".join(value.split())


def parse_wikitext(text: str) -> dict[str, Any]:
    """Return a JSON-friendly structural view of arbitrary wikitext.

    No domain meaning is assigned. The result only describes syntax found in
    the source: headings/sections, template calls and arbitrary parameters,
    wiki/external links, list items and wiki tables.
    """
    source = str(text)
    return {
        "sections": extract_sections(source),
        "templates": extract_template_calls(source),
        "wikilinks": extract_wikilinks(source),
        "external_links": extract_external_links(source),
        "lists": extract_lists(source),
        "tables": extract_tables(source),
    }


def extract_sections(text: str) -> list[dict[str, Any]]:
    masked = _mask_protected(text)
    matches = list(_HEADING_RE.finditer(masked))
    result: list[dict[str, Any]] = []

    for index, match in enumerate(matches):
        level = len(match.group(1))
        raw_heading = text[match.start(2):match.end(2)].strip()
        section_end = len(text)

        for later in matches[index + 1 :]:
            if len(later.group(1)) <= level:
                section_end = later.start()
                break

        result.append(
            {
                "index": index + 1,
                "level": level,
                "heading": {
                    "raw": raw_heading,
                    "text": clean_wikitext(raw_heading),
                },
                "start": match.start(),
                "content_start": match.end(),
                "end": section_end,
            }
        )

    return result


def extract_lists(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    raw_lines = text.splitlines()
    masked_lines = _mask_protected(text).splitlines()
    for line_number, (raw_line, masked_line) in enumerate(zip(raw_lines, masked_lines), start=1):
        match = _LIST_RE.match(masked_line)
        if not match:
            continue
        markers = match.group(1)
        content_start = match.start(2)
        raw_content = raw_line[content_start:].strip()
        items.append(
            {
                "line": line_number,
                "depth": len(markers),
                "markers": markers,
                "marker": markers[-1],
                "raw": raw_line,
                "value": {
                    "raw": raw_content,
                    "text": clean_wikitext(raw_content),
                },
            }
        )
    return items


def extract_wikilinks(text: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    masked = _mask_protected(text)
    for match in _WIKILINK_RE.finditer(masked):
        raw = text[match.start():match.end()]
        inner = raw[2:-2]
        parts = [part.strip() for part in inner.split("|")]
        target = parts[0] if parts else ""
        label = parts[-1] if len(parts) > 1 else target
        links.append(
            {
                "raw": raw,
                "target": target,
                "label": label,
                "text": clean_wikitext(label),
                "span": [match.start(), match.end()],
            }
        )
    return links


def extract_external_links(text: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    masked = _mask_protected(text)
    for match in _EXTERNAL_LINK_RE.finditer(masked):
        raw = text[match.start():match.end()]
        original_match = _EXTERNAL_LINK_RE.fullmatch(raw)
        if original_match is None:
            continue
        url = original_match.group(1)
        label = original_match.group(2)
        links.append(
            {
                "raw": raw,
                "url": url,
                "label": label.strip() if label else None,
                "text": clean_wikitext(label) if label else url,
                "span": [match.start(), match.end()],
            }
        )
    return links


def extract_template_calls(text: str) -> list[dict[str, Any]]:
    """Extract nested template calls without assigning meaning to them."""
    result: list[dict[str, Any]] = []

    masked = _mask_protected(text)
    for start, end, depth in _template_spans(masked):
        raw = text[start:end]
        inner = raw[2:-2]
        parts = _split_top_level(inner, "|")
        if not parts:
            continue

        name_raw = parts[0].strip()
        parameters: list[dict[str, Any]] = []
        params: dict[str, str] = {}
        params_clean: dict[str, str] = {}
        positional = 1

        for part in parts[1:]:
            split = _split_first_top_level(part, "=")
            if split is None:
                key = str(positional)
                positional += 1
                value = part.strip()
                explicit = False
            else:
                key, value = split
                key = key.strip()
                value = value.strip()
                explicit = True
                if not key:
                    key = str(positional)
                    positional += 1
                    explicit = False

            clean_value = clean_wikitext(value)
            parameters.append(
                {
                    "name": key,
                    "explicit": explicit,
                    "value": {
                        "raw": value,
                        "text": clean_value,
                    },
                }
            )
            params[key] = value
            params_clean[key] = clean_value

        result.append(
            {
                "name": clean_wikitext(name_raw),
                "name_raw": name_raw,
                "depth": depth,
                "span": [start, end],
                "raw": raw,
                "parameters": parameters,
                "params": params,
                "params_clean": params_clean,
            }
        )

    result.sort(key=lambda item: (item["span"][0], item["depth"]))
    return result


def extract_tables(text: str) -> list[dict[str, Any]]:
    """Extract MediaWiki table blocks and conservatively parse rows/cells."""
    raw_lines = text.splitlines(keepends=True)
    masked_lines = _mask_protected(text).splitlines(keepends=True)
    tables: list[dict[str, Any]] = []
    current: list[str] | None = None
    start_line = 0
    depth = 0

    for line_number, (raw_line, masked_line) in enumerate(zip(raw_lines, masked_lines), start=1):
        stripped = masked_line.lstrip()
        if stripped.startswith("{|"):
            if current is None:
                current = []
                start_line = line_number
                depth = 0
            depth += 1

        if current is not None:
            current.append(raw_line)

        if current is not None and stripped.startswith("|}"):
            depth -= 1
            if depth <= 0:
                raw = "".join(current)
                tables.append(
                    {
                        "start_line": start_line,
                        "end_line": line_number,
                        "raw": raw,
                        "rows": _parse_table_rows(raw),
                    }
                )
                current = None
                depth = 0

    return tables


def _parse_table_rows(raw: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_cells: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current_cells
        if current_cells:
            rows.append({"cells": current_cells})
            current_cells = []

    for line in raw.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("{|" ) or stripped.startswith("|}"):
            continue
        if stripped.startswith("|-"):
            flush()
            continue

        if stripped.startswith("!"):
            content = stripped[1:]
            pieces = _split_top_level(content, "!!")
            for piece in pieces:
                current_cells.append(_table_cell(piece, header=True))
        elif stripped.startswith("|"):
            content = stripped[1:]
            pieces = _split_top_level(content, "||")
            for piece in pieces:
                current_cells.append(_table_cell(piece, header=False))

    flush()
    return rows


def _table_cell(value: str, *, header: bool) -> dict[str, Any]:
    raw = value.strip()
    # Attributes commonly precede the cell value and are separated by a single
    # top-level pipe. Preserve the whole raw value, but expose the likely value
    # portion for convenience.
    split = _split_first_top_level(raw, "|")
    content = split[1].strip() if split is not None else raw
    return {
        "header": header,
        "raw": raw,
        "value": {
            "raw": content,
            "text": clean_wikitext(content),
        },
    }


def _mask_protected(text: str) -> str:
    """Mask comments/nowiki-like regions while preserving length/newlines."""
    chars = list(text)
    for match in _PROTECTED_RE.finditer(text):
        for index in range(match.start(), match.end()):
            if chars[index] not in "\r\n":
                chars[index] = " "
    return "".join(chars)


def _template_spans(text: str) -> list[tuple[int, int, int]]:
    """Return ``(start, end, template_depth)`` for balanced ``{{...}}`` calls.

    Triple-brace template arguments are tracked so their delimiters do not
    accidentally close surrounding templates.
    """
    stack: list[tuple[str, int, int]] = []
    spans: list[tuple[int, int, int]] = []
    i = 0

    while i < len(text):
        if text.startswith("{{{", i):
            stack.append(("argument", i, 0))
            i += 3
            continue
        if text.startswith("{{", i):
            depth = 1 + sum(1 for kind, _start, _depth in stack if kind == "template")
            stack.append(("template", i, depth))
            i += 2
            continue
        if text.startswith("}}}", i) and stack and stack[-1][0] == "argument":
            stack.pop()
            i += 3
            continue
        if text.startswith("}}", i) and stack and stack[-1][0] == "template":
            _kind, start, depth = stack.pop()
            spans.append((start, i + 2, depth))
            i += 2
            continue
        i += 1

    spans.sort(key=lambda item: (item[0], item[2]))
    return spans


def _split_top_level(text: str, separator: str) -> list[str]:
    if not separator:
        raise ValueError("separator must not be empty")

    parts: list[str] = []
    start = 0
    template_depth = 0
    argument_depth = 0
    link_depth = 0
    i = 0

    while i < len(text):
        if text.startswith("{{{", i):
            argument_depth += 1
            i += 3
            continue
        if text.startswith("{{", i):
            template_depth += 1
            i += 2
            continue
        if text.startswith("}}}", i) and argument_depth:
            argument_depth -= 1
            i += 3
            continue
        if text.startswith("}}", i) and template_depth:
            template_depth -= 1
            i += 2
            continue
        if text.startswith("[[", i):
            link_depth += 1
            i += 2
            continue
        if text.startswith("]]", i) and link_depth:
            link_depth -= 1
            i += 2
            continue

        if (
            template_depth == 0
            and argument_depth == 0
            and link_depth == 0
            and text.startswith(separator, i)
        ):
            parts.append(text[start:i])
            i += len(separator)
            start = i
            continue

        i += 1

    parts.append(text[start:])
    return parts


def _split_first_top_level(text: str, separator: str) -> tuple[str, str] | None:
    parts = _split_top_level(text, separator)
    if len(parts) < 2:
        return None
    return parts[0], separator.join(parts[1:])
