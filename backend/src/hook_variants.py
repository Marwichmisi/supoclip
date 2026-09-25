"""Hook variant normalization shared by analysis, persistence, and editing.

A hook variant is the plain-text title burned into the first seconds of a clip.
The model may provide several candidates, but the public contract always exposes
three usable French titles and one selected title for rendering.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Iterable

HOOK_TITLE_MAX_CHARS = 64
HOOK_TITLE_MAX_WORDS = 10
HOOK_VARIANT_COUNT = 3

_FILLER_WORDS = {
    "a",
    "au",
    "aux",
    "avec",
    "ce",
    "ces",
    "dans",
    "de",
    "des",
    "du",
    "elle",
    "en",
    "et",
    "il",
    "ils",
    "je",
    "la",
    "le",
    "les",
    "mais",
    "me",
    "ne",
    "nos",
    "notre",
    "nous",
    "on",
    "ou",
    "par",
    "pas",
    "pour",
    "quand",
    "que",
    "qui",
    "sa",
    "se",
    "ses",
    "son",
    "sur",
    "ta",
    "te",
    "tu",
    "un",
    "une",
    "vos",
    "votre",
    "vous",
}


def sanitize_hook_title(raw: Any) -> str | None:
    """Return a bounded, renderable plain-text hook title.

    Quotes, hashtags and presentation punctuation are removed, but ``?`` and
    ``!`` are retained because they are meaningful for a question or energetic
    hook.  The result is deliberately short enough for the top safe area of a
    1080-wide cover.
    """
    if raw is None:
        return None
    title = str(raw).strip()
    title = title.strip("\"'`“”‘’").strip()
    title = re.sub(r"#\w+", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    title = title.rstrip(".,;:-–— ").strip()
    if not title:
        return None

    words = title.split()
    if len(words) > HOOK_TITLE_MAX_WORDS:
        title = " ".join(words[:HOOK_TITLE_MAX_WORDS])
    if len(title) > HOOK_TITLE_MAX_CHARS:
        clipped = title[: HOOK_TITLE_MAX_CHARS + 1]
        cut = clipped.rfind(" ")
        title = (clipped[:cut] if cut > 20 else title[:HOOK_TITLE_MAX_CHARS]).rstrip(
            ".,;:-–— "
        )
    return title or None


def _variant_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _segment_subject(segment_text: str) -> str:
    cleaned_text = re.sub(r"^\s*\[[^\]]+\]\s*", "", segment_text or "")
    words = re.findall(r"[\wÀ-ÿ'-]+", cleaned_text, flags=re.UNICODE)
    meaningful = [word for word in words if word.casefold() not in _FILLER_WORDS]
    if not meaningful:
        meaningful = words
    # A short subject keeps the generated headline readable after the template
    # prefix and ASS line balancing are applied.
    subject = " ".join(meaningful[:7]).strip(" .,;:!?-–—")
    return subject[:48].rstrip(" .,;:!?-–—").strip() or "ce moment clé"


def _derived_candidates(segment_text: str, hook_type: str | None) -> list[str]:
    subject = _segment_subject(segment_text)
    kind = (hook_type or "statement").casefold()
    if kind == "question":
        return [
            f"Pourquoi {subject} ?",
            f"Ce que {subject} change",
            f"La question derrière {subject}",
        ]
    if kind == "statistic":
        return [
            f"Le chiffre qui change {subject}",
            f"Ce que révèle {subject}",
            f"Comprendre {subject} en un clip",
        ]
    if kind == "story":
        return [
            f"L'histoire derrière {subject}",
            f"Ce qui a changé {subject}",
            f"Pourquoi {subject} compte",
        ]
    if kind == "contrast":
        return [
            f"Avant / après : {subject}",
            f"Ce que {subject} révèle",
            f"La lesson de {subject}",
        ]
    return [
        f"{subject} : le point clé",
        f"Ce que {subject} révèle",
        f"À retenir sur {subject}",
    ]


def build_hook_variants(
    hook_title: Any = None,
    segment_text: str = "",
    hook_type: str | None = None,
    provided_variants: Iterable[Any] | None = None,
) -> list[str]:
    """Build exactly three unique, sanitized French hook variants.

    ``hook_title`` remains first for backwards compatibility with existing
    clips.  Extra model candidates are considered next, and deterministic
    grounded candidates fill the remaining slots for local models that only
    return one title.
    """
    candidates: list[Any] = [hook_title]
    candidates.extend(list(provided_variants or []))

    variants: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        title = sanitize_hook_title(candidate)
        if not title:
            continue
        key = _variant_key(title)
        if key in seen:
            continue
        seen.add(key)
        variants.append(title)
        if len(variants) == HOOK_VARIANT_COUNT:
            return variants

    for candidate in _derived_candidates(segment_text, hook_type):
        title = sanitize_hook_title(candidate)
        if not title:
            continue
        key = _variant_key(title)
        if key in seen:
            continue
        seen.add(key)
        variants.append(title)
        if len(variants) == HOOK_VARIANT_COUNT:
            break

    # A very short/empty segment can make the templates collapse to the same
    # text. Keep the API contract deterministic rather than returning fewer
    # than three choices.
    subject = _segment_subject(segment_text)
    suffix = 2
    while len(variants) < HOOK_VARIANT_COUNT:
        title = sanitize_hook_title(f"Option {suffix} : {subject}")
        suffix += 1
        if title:
            key = _variant_key(title)
            if key not in seen:
                seen.add(key)
                variants.append(title)
    return variants[:HOOK_VARIANT_COUNT]


def select_hook_title(
    variants: Iterable[Any],
    selected_index: Any = None,
    fallback_title: Any = None,
) -> str | None:
    """Resolve the title to burn while preserving a hand-edited override."""
    normalized = [
        title
        for candidate in variants or []
        if (title := sanitize_hook_title(candidate))
    ]
    if (
        isinstance(selected_index, int)
        and not isinstance(selected_index, bool)
        and 0 <= selected_index < len(normalized)
    ):
        return normalized[selected_index]
    return sanitize_hook_title(fallback_title) or (normalized[0] if normalized else None)


def resolve_segment_hook(
    segment: Mapping[str, Any],
) -> tuple[list[str], str | None]:
    """Return the three choices and the title that should be rendered."""
    stored_variants = segment.get("hook_variants") or []
    variants = build_hook_variants(
        stored_variants[0] if stored_variants else segment.get("hook_title"),
        segment.get("text") or "",
        segment.get("hook_type"),
        stored_variants,
    )
    return variants, select_hook_title(
        variants,
        segment.get("selected_hook_variant"),
        segment.get("hook_title"),
    )


def serialize_hook_variants(variants: Iterable[Any] | None) -> str:
    """Serialize variants for the legacy TEXT column using stable JSON."""
    import json

    safe = [variant for variant in (variants or []) if isinstance(variant, str) and variant]
    return json.dumps(safe[:HOOK_VARIANT_COUNT], ensure_ascii=False)


def deserialize_hook_variants(value: Any) -> list[str]:
    """Decode a stored hook_variants value, ignoring malformed legacy rows."""
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, str) and item.strip()][:HOOK_VARIANT_COUNT]
    if not isinstance(value, str) or not value.strip():
        return []
    import json

    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return [value] if value.strip() else []
    if isinstance(decoded, list):
        return [str(item) for item in decoded if isinstance(item, str) and item.strip()][:HOOK_VARIANT_COUNT]
    return [str(decoded)]
