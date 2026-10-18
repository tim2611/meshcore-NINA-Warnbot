"""Kernlogik: abrufen, filtern, entscheiden, senden."""

from __future__ import annotations

import asyncio
import logging

from .config import Config
from .filters import passes, severity_rank
from .formatter import render
from .nina import Alert, NinaClient, NinaError
from .sender import SendError
from .state import State

log = logging.getLogger(__name__)


class WarnBot:
    def __init__(self, cfg: Config, client: NinaClient, sender, state: State):
        self.cfg = cfg
        self.client = client
        self.sender = sender
        self.state = state
        self._sleep = asyncio.sleep  # in Tests austauschbar

    async def fetch_all(self) -> dict[str, Alert]:
        """Holt alle Kreise. Gleiche Warnungen in mehreren Kreisen zählen einmal."""
        alerts: dict[str, Alert] = {}
        errors = 0
        for ars in self.cfg.nina.ars:
            try:
                for a in await self.client.dashboard(ars):
                    alerts.setdefault(a.id, a)
            except NinaError as exc:
                errors += 1
                log.warning("NINA-Abruf für %s fehlgeschlagen: %s", ars, exc)
        if errors == len(self.cfg.nina.ars):
            raise NinaError("Kein Kreis abrufbar")
        return alerts

    def _decide(self, alert: Alert) -> str | None:
        """Gibt "new", "update" oder "cancel" zurück – oder None, wenn nichts zu tun ist."""
        prev = self.state.get(alert.id)
        if alert.is_cancel:
            if (
                self.cfg.bot.announce_cancel
                and prev
                and prev.get("announced")
                and not prev.get("silent")
                and not prev.get("cancel_announced")
            ):
                return "cancel"
            return None
        if not passes(alert, self.cfg.filter):
            return None
        if prev is None or not prev.get("announced"):
            return "new"
        if prev.get("hash") == alert.hash:
            return None
        mode = self.cfg.bot.announce_updates
        if mode == "always":
            return "update"
        if mode == "severity" and severity_rank(alert.severity) > prev.get("rank", 0):
            return "update"
        return None

    async def run_cycle(self, silent: bool = False) -> int:
        """Ein Durchlauf. Mit silent=True wird nur gemerkt, nicht gesendet
        (Erststart ohne announce_existing_on_start). Gibt die Zahl gesendeter
        Funknachrichten zurück."""
        alerts = await self.fetch_all()
        self.state.touch(alerts.keys())

        # Wichtigstes zuerst, bei gleicher Stufe das Neueste
        ordered = sorted(
            alerts.values(),
            key=lambda a: (severity_rank(a.severity), a.sent.timestamp() if a.sent else 0),
            reverse=True,
        )

        budget = self.cfg.mesh.max_messages_per_cycle
        sent = 0
        for alert in ordered:
            action = self._decide(alert)
            if action is None:
                # Nicht weiterleiten, aber Hash/Stufe aktuell halten, sofern bekannt
                prev = self.state.get(alert.id)
                if prev and prev.get("announced") and not alert.is_cancel:
                    prev["hash"] = alert.hash
                continue

            if silent:
                self.state.put(alert.id, hash=alert.hash, rank=severity_rank(alert.severity),
                               announced=True, silent=True)
                continue

            if action != "cancel":
                alert = await self.client.enrich(alert)
            parts = render(alert, self.cfg.message, self.cfg.nina.region_label)
            if sent and sent + len(parts) > budget:
                log.info("Sendebudget erschöpft – %s folgt im nächsten Durchlauf", alert.id)
                break

            for part in parts:
                if sent:
                    await self._sleep(self.cfg.mesh.send_delay)
                await self.sender.send(part)  # SendError bricht ab, Zustand bleibt unverändert
                sent += 1

            if action == "cancel":
                self.state.put(alert.id, cancel_announced=True)
            else:
                self.state.put(alert.id, hash=alert.hash, rank=severity_rank(alert.severity),
                               announced=True, silent=False)
            self.state.save()  # nach jeder Meldung, damit ein Absturz nichts doppelt sendet
            log.info("%s: %s (%d Teil(e))", action, alert.id, len(parts))

        removed = self.state.prune()
        if removed:
            log.debug("%d alte Einträge vergessen", removed)
        self.state.save()
        return sent


__all__ = ["WarnBot", "SendError"]
