"""Merkt sich, welche Warnungen bereits gesendet wurden (JSON-Datei)."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Einträge, die so lange nicht mehr im Dashboard auftauchten, werden vergessen
FORGET_AFTER = 7 * 24 * 3600


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.entries: dict[str, dict] = {}
        self.existed = False

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = data.get("alerts", {}) if isinstance(data, dict) else {}
            self.existed = True
        except FileNotFoundError:
            self.entries = {}
            self.existed = False
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Zustandsdatei %s unlesbar (%s) – starte leer", self.path, exc)
            self.entries = {}
            self.existed = True  # nicht als Erststart behandeln, sonst Flut

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "alerts": self.entries}, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def get(self, alert_id: str) -> dict | None:
        return self.entries.get(alert_id)

    def put(self, alert_id: str, **fields) -> None:
        entry = self.entries.setdefault(alert_id, {})
        entry.update(fields)
        entry["last_seen"] = int(time.time())

    def touch(self, alert_ids) -> None:
        now = int(time.time())
        for aid in alert_ids:
            if aid in self.entries:
                self.entries[aid]["last_seen"] = now

    def prune(self, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        old = [k for k, v in self.entries.items() if now - v.get("last_seen", 0) > FORGET_AFTER]
        for k in old:
            del self.entries[k]
        return len(old)
