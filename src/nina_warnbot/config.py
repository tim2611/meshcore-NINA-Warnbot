"""Konfiguration laden und prüfen (TOML)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Reihenfolge = Rangfolge (aufsteigend)
SEVERITIES = ["Minor", "Moderate", "Severe", "Extreme"]

# Präfix der Warnungs-ID -> Anzeigename
PROVIDERS = {
    "mow": "MoWaS",
    "kat": "KATWARN",
    "biw": "BIWAPP",
    "dwd": "DWD",
    "lhp": "Hochwasser",
    "pol": "Polizei",
}

UPDATE_MODES = ("never", "severity", "always")
CONNECTIONS = ("serial", "tcp", "ble")

MIN_POLL_INTERVAL = 60  # Sekunden – den BBK-Server nicht überlasten


class ConfigError(ValueError):
    """Ungültige oder unvollständige Konfiguration."""


def normalize_ars(value: object) -> str:
    """Bringt einen Regionalschlüssel auf Kreisebene (5 Stellen + 7 Nullen).

    Die NINA-API liefert Daten nur auf Kreisebene, daher werden die
    letzten 7 Stellen immer durch Nullen ersetzt.
    """
    s = str(value).strip().replace(" ", "")
    if not s.isdigit() or not 5 <= len(s) <= 12:
        raise ConfigError(
            f"Ungültiger Regionalschlüssel {value!r}: erwartet 5 bis 12 Ziffern, "
            "z. B. 072330000000 für den Landkreis Vulkaneifel"
        )
    return s[:5] + "0" * 7


@dataclass
class NinaConfig:
    ars: list[str]
    region_label: str = ""
    poll_interval: int = 120
    base_url: str = "https://warnung.bund.de/api31"
    timeout: float = 20.0
    user_agent: str = "meshcore-nina-warnbot/0.1"


@dataclass
class FilterConfig:
    min_severity: str = "Moderate"
    providers: list[str] = field(default_factory=lambda: list(PROVIDERS))
    exclude_events: list[str] = field(default_factory=list)


@dataclass
class MessageConfig:
    prefix: str = "⚠️"
    max_bytes: int = 130
    max_parts: int = 2
    transliterate: bool = False
    include_region: bool = True
    include_expires: bool = True
    timezone: str = "Europe/Berlin"


@dataclass
class MeshConfig:
    connection: str = "serial"
    serial_port: str = "/dev/ttyUSB0"
    # Gesetzt: Port des Companions mit diesem Namen suchen, serial_port zuerst
    node_name: str = ""
    baudrate: int = 115200
    host: str = ""
    tcp_port: int = 5000
    ble_address: str = ""
    ble_pin: str = ""
    channel: str = ""
    channel_index: int | None = None
    max_channels: int = 16
    send_delay: float = 5.0
    max_messages_per_cycle: int = 4
    # Leer = Verbindung dauerhaft halten. Sonst nur zum Senden verbinden,
    # unter diesem flock (für Nodes, die sich mehrere Programme teilen)
    lock_file: str = ""
    lock_timeout: float = 120.0


@dataclass
class BotConfig:
    state_file: str = "/var/lib/nina-warnbot/state.json"
    announce_existing_on_start: bool = False
    announce_updates: str = "severity"
    announce_cancel: bool = True
    dry_run: bool = False
    log_level: str = "INFO"


@dataclass
class Config:
    nina: NinaConfig
    filter: FilterConfig
    message: MessageConfig
    mesh: MeshConfig
    bot: BotConfig


def _section(raw: dict, name: str) -> dict:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"Abschnitt [{name}] muss eine Tabelle sein")
    return value


def _build(cls, data: dict, section: str):
    known = cls.__dataclass_fields__.keys()
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(
            f"Unbekannte Einstellung(en) in [{section}]: {', '.join(sorted(unknown))}"
        )
    return cls(**data)


def parse_config(raw: dict) -> Config:
    nina_raw = dict(_section(raw, "nina"))
    ars = nina_raw.get("ars")
    if ars is None:
        raise ConfigError("[nina] ars fehlt – bitte mindestens einen Regionalschlüssel angeben")
    if not isinstance(ars, list):
        ars = [ars]
    if not ars:
        raise ConfigError("[nina] ars ist leer")
    # Doppelte Kreise entfernen, Reihenfolge behalten
    nina_raw["ars"] = list(dict.fromkeys(normalize_ars(a) for a in ars))

    cfg = Config(
        nina=_build(NinaConfig, nina_raw, "nina"),
        filter=_build(FilterConfig, dict(_section(raw, "filter")), "filter"),
        message=_build(MessageConfig, dict(_section(raw, "message")), "message"),
        mesh=_build(MeshConfig, dict(_section(raw, "mesh")), "mesh"),
        bot=_build(BotConfig, dict(_section(raw, "bot")), "bot"),
    )
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.nina.poll_interval < MIN_POLL_INTERVAL:
        raise ConfigError(
            f"[nina] poll_interval muss mindestens {MIN_POLL_INTERVAL} Sekunden betragen"
        )
    if cfg.filter.min_severity not in SEVERITIES:
        raise ConfigError(
            f"[filter] min_severity muss einer von {', '.join(SEVERITIES)} sein"
        )
    bad = [p for p in cfg.filter.providers if p not in PROVIDERS]
    if bad:
        raise ConfigError(
            f"[filter] providers enthält Unbekanntes: {', '.join(bad)} "
            f"(erlaubt: {', '.join(PROVIDERS)})"
        )
    if not 40 <= cfg.message.max_bytes <= 200:
        raise ConfigError("[message] max_bytes muss zwischen 40 und 200 liegen")
    if not 1 <= cfg.message.max_parts <= 5:
        raise ConfigError("[message] max_parts muss zwischen 1 und 5 liegen")
    if cfg.mesh.connection not in CONNECTIONS:
        raise ConfigError(f"[mesh] connection muss einer von {', '.join(CONNECTIONS)} sein")
    if cfg.mesh.connection == "tcp" and not cfg.mesh.host:
        raise ConfigError("[mesh] host fehlt für connection = \"tcp\"")
    if cfg.mesh.connection == "ble" and not cfg.mesh.ble_address:
        raise ConfigError("[mesh] ble_address fehlt für connection = \"ble\"")
    if not cfg.mesh.channel and cfg.mesh.channel_index is None:
        raise ConfigError("[mesh] bitte channel (Name) oder channel_index angeben")
    if cfg.mesh.send_delay < 1:
        raise ConfigError("[mesh] send_delay muss mindestens 1 Sekunde betragen")
    if cfg.mesh.max_messages_per_cycle < 1:
        raise ConfigError("[mesh] max_messages_per_cycle muss mindestens 1 sein")
    if cfg.mesh.lock_timeout < 1:
        raise ConfigError("[mesh] lock_timeout muss mindestens 1 Sekunde betragen")
    if cfg.bot.announce_updates not in UPDATE_MODES:
        raise ConfigError(
            f"[bot] announce_updates muss einer von {', '.join(UPDATE_MODES)} sein"
        )


def load_config(path: str | Path) -> Config:
    p = Path(path)
    try:
        with p.open("rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ConfigError(f"Konfigurationsdatei nicht gefunden: {p}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Fehler in {p}: {exc}") from exc
    return parse_config(raw)
