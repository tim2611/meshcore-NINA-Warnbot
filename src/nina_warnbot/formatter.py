"""Warnungen in kurze, funktaugliche Textnachrichten umwandeln."""

from __future__ import annotations

import unicodedata
from zoneinfo import ZoneInfo

from .config import MessageConfig
from .nina import Alert

SEVERITY_LABELS = {
    "Extreme": "EXTREM",
    "Severe": "HOCH",
    "Moderate": "MITTEL",
    "Minor": "GERING",
}

_TRANSLIT = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
    "–": "-", "—": "-", "„": '"', "“": '"', "”": '"', "‚": "'", "‘": "'", "’": "'",
    "…": "...",
})


def transliterate(text: str) -> str:
    """Umlaute ausschreiben und alles Nicht-ASCII entfernen (spart Bytes)."""
    text = text.translate(_TRANSLIT)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def build_text(alert: Alert, cfg: MessageConfig, region_label: str = "") -> str:
    prefix = f"{cfg.prefix} " if cfg.prefix else ""
    if alert.is_cancel:
        text = f"{prefix}Entwarnung {alert.provider_label}: {alert.headline}"
    else:
        sev = SEVERITY_LABELS.get(alert.severity, alert.severity.upper())
        text = f"{prefix}{alert.provider_label} [{sev}] {alert.headline}"
    if cfg.include_region and region_label:
        text += f" – {region_label}"
    if cfg.include_expires and alert.expires and not alert.is_cancel:
        local = alert.expires.astimezone(ZoneInfo(cfg.timezone))
        text += f", bis {local:%d.%m. %H:%M}"
    if cfg.transliterate:
        text = transliterate(text)
    return text


def _blen(s: str) -> int:
    return len(s.encode("utf-8"))


def _hard_split(word: str, limit: int) -> list[str]:
    """Zerlegt ein überlanges Wort zeichenweise in Stücke ≤ limit Bytes."""
    pieces, cur = [], ""
    for ch in word:
        if cur and _blen(cur + ch) > limit:
            pieces.append(cur)
            cur = ""
        cur += ch
    if cur:
        pieces.append(cur)
    return pieces


def split_message(text: str, max_bytes: int, max_parts: int) -> list[str]:
    """Teilt Text an Wortgrenzen in höchstens max_parts Nachrichten ≤ max_bytes.

    Bei mehreren Teilen wird " (1/2)" angehängt. Passt der Text nicht in
    max_parts Teile, wird der letzte Teil mit "…" gekürzt.
    """
    text = " ".join(text.split())
    if _blen(text) <= max_bytes:
        return [text]
    if max_parts == 1:
        return [_truncate(text, max_bytes)]

    suffix_len = _blen(f" ({max_parts}/{max_parts})")
    limit = max_bytes - suffix_len
    words: list[str] = []
    for w in text.split(" "):
        words.extend(_hard_split(w, limit) if _blen(w) > limit else [w])

    chunks, cur = [], ""
    for w in words:
        candidate = f"{cur} {w}" if cur else w
        if _blen(candidate) <= limit:
            cur = candidate
        else:
            chunks.append(cur)
            cur = w
    if cur:
        chunks.append(cur)

    if len(chunks) > max_parts:
        rest = " ".join(chunks[max_parts - 1:])
        chunks = chunks[: max_parts - 1] + [_truncate(rest, limit)]

    n = len(chunks)
    return [f"{c} ({i}/{n})" for i, c in enumerate(chunks, 1)]


def _truncate(text: str, max_bytes: int) -> str:
    if _blen(text) <= max_bytes:
        return text
    # Bei reinem ASCII-Text (transliterate) kein Unicode-Zeichen einschmuggeln
    ell = "..." if text.isascii() else "…"
    out = ""
    for ch in text:
        if _blen(out + ch + ell) > max_bytes:
            break
        out += ch
    return out.rstrip() + ell


def render(alert: Alert, cfg: MessageConfig, region_label: str = "") -> list[str]:
    return split_message(build_text(alert, cfg, region_label), cfg.max_bytes, cfg.max_parts)
