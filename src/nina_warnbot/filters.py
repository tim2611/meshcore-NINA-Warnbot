"""Entscheidet, welche Warnungen ins Mesh gehen."""

from __future__ import annotations

from .config import SEVERITIES, FilterConfig
from .nina import Alert


def severity_rank(severity: str) -> int:
    """Unbekannte Stufen zählen wie "Minor", damit nichts Wichtiges verschluckt wird,
    aber auch nichts Unklares bevorzugt."""
    try:
        return SEVERITIES.index(severity)
    except ValueError:
        return 0


def passes(alert: Alert, cfg: FilterConfig) -> bool:
    if alert.provider not in cfg.providers:
        return False
    if alert.event_code and alert.event_code in cfg.exclude_events:
        return False
    return severity_rank(alert.severity) >= severity_rank(cfg.min_severity)
