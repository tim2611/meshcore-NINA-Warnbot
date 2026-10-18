"""Zugriff auf die NINA-API des Bundesamts für Bevölkerungsschutz (BBK).

Dokumentation: https://nina.api.bund.dev
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime
from urllib.parse import quote

from .config import PROVIDERS

log = logging.getLogger(__name__)


class NinaError(RuntimeError):
    """Abruf oder Auswertung der NINA-API fehlgeschlagen."""


@dataclass(frozen=True)
class Alert:
    id: str
    provider: str          # Kurzschlüssel, z. B. "mow", "dwd"
    headline: str
    severity: str          # Minor | Moderate | Severe | Extreme | Unknown
    msg_type: str          # Alert | Update | Cancel
    hash: str              # ändert sich, sobald sich die Meldung ändert
    sent: datetime | None = None
    event_code: str | None = None
    expires: datetime | None = None
    description: str = ""
    instruction: str = ""

    @property
    def provider_label(self) -> str:
        return PROVIDERS.get(self.provider, self.provider.upper())

    @property
    def is_cancel(self) -> bool:
        return self.msg_type.lower() == "cancel"


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _clean(text: object) -> str:
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())


def parse_dashboard(items: object) -> list[Alert]:
    """Wandelt die Antwort von /dashboard/{ARS}.json in Alert-Objekte um."""
    if not isinstance(items, list):
        raise NinaError("Unerwartetes Format: Dashboard ist keine Liste")
    alerts: list[Alert] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        payload = item.get("payload") or {}
        data = payload.get("data") or {}
        alert_id = item.get("id") or payload.get("id")
        if not alert_id:
            continue
        headline = _clean((item.get("i18nTitle") or {}).get("de")) or _clean(data.get("headline"))
        severity = data.get("severity") or "Unknown"
        msg_type = data.get("msgType") or "Alert"
        h = payload.get("hash") or hashlib.sha256(
            f"{headline}|{severity}|{msg_type}".encode()
        ).hexdigest()
        alerts.append(
            Alert(
                id=alert_id,
                provider=alert_id.split(".", 1)[0].lower(),
                headline=headline or "(ohne Titel)",
                severity=severity,
                msg_type=msg_type,
                hash=h,
                sent=_parse_dt(item.get("sent")),
                event_code=(data.get("transKeys") or {}).get("event"),
            )
        )
    return alerts


def apply_details(alert: Alert, details: object) -> Alert:
    """Ergänzt eine Meldung um Ablaufzeit und Texte aus /warnings/{id}.json (CAP)."""
    if not isinstance(details, dict):
        return alert
    infos = [i for i in details.get("info") or [] if isinstance(i, dict)]
    if not infos:
        return alert
    info = next(
        (i for i in infos if str(i.get("language", "")).lower().startswith("de")),
        infos[0],
    )
    return replace(
        alert,
        headline=_clean(info.get("headline")) or alert.headline,
        expires=_parse_dt(info.get("expires")),
        description=_clean(info.get("description")),
        instruction=_clean(info.get("instruction")),
    )


class NinaClient:
    def __init__(self, base_url: str, timeout: float, user_agent: str):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.user_agent = user_agent

    def _get_json_sync(self, path: str) -> object:
        url = f"{self.base_url}/{path.lstrip('/')}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise NinaError(f"Abruf von {url} fehlgeschlagen: {exc}") from exc

    async def get_json(self, path: str) -> object:
        return await asyncio.to_thread(self._get_json_sync, path)

    async def dashboard(self, ars: str) -> list[Alert]:
        return parse_dashboard(await self.get_json(f"dashboard/{ars}.json"))

    async def enrich(self, alert: Alert) -> Alert:
        """Lädt Details nach. Schlägt das fehl, bleibt die Kurzform erhalten."""
        try:
            details = await self.get_json(f"warnings/{quote(alert.id, safe='.-_')}.json")
        except NinaError as exc:
            log.warning("Details zu %s nicht abrufbar: %s", alert.id, exc)
            return alert
        return apply_details(alert, details)
