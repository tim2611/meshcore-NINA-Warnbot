"""Versand in einen MeshCore-Kanal über einen Companion-Node."""

from __future__ import annotations

import logging

from .config import MeshConfig

log = logging.getLogger(__name__)


class SendError(RuntimeError):
    """Nachricht konnte nicht übergeben werden."""


class DryRunSender:
    """Gibt Nachrichten nur im Log aus – zum Testen ohne Funk."""

    def __init__(self):
        self.sent: list[str] = []

    @property
    def connected(self) -> bool:
        return True

    async def connect(self, resolve_channel: bool = True) -> None:
        log.info("Testlauf: es wird nichts gefunkt")

    async def send(self, text: str) -> None:
        self.sent.append(text)
        log.info("[TESTLAUF] %s (%d Bytes)", text, len(text.encode("utf-8")))

    async def list_channels(self) -> list[tuple[int, str]]:
        return []

    async def close(self) -> None:
        pass


class MeshCoreSender:
    def __init__(self, cfg: MeshConfig):
        self.cfg = cfg
        self.mc = None
        self.channel_idx: int | None = None

    @property
    def connected(self) -> bool:
        return self.mc is not None

    async def connect(self, resolve_channel: bool = True) -> None:
        from meshcore import MeshCore  # spät importieren, damit Tests ohne Funk laufen

        c = self.cfg
        if c.connection == "serial":
            log.info("Verbinde seriell mit %s", c.serial_port)
            mc = await MeshCore.create_serial(c.serial_port, c.baudrate)
        elif c.connection == "tcp":
            log.info("Verbinde per TCP mit %s:%s", c.host, c.tcp_port)
            mc = await MeshCore.create_tcp(c.host, c.tcp_port)
        else:
            log.info("Verbinde per Bluetooth mit %s", c.ble_address)
            mc = await MeshCore.create_ble(c.ble_address, pin=c.ble_pin or None)
        if mc is None:
            raise SendError("Keine Antwort vom Node – ist es ein Companion-Node mit passender Firmware?")
        self.mc = mc
        if not resolve_channel:
            return
        try:
            self.channel_idx = await self._resolve_channel()
        except BaseException:
            await self.close()
            raise
        log.info("Sende in Kanal %d", self.channel_idx)

    async def list_channels(self) -> list[tuple[int, str]]:
        from meshcore import EventType

        found = []
        for idx in range(self.cfg.max_channels):
            ev = await self.mc.commands.get_channel(idx)
            if ev is None or ev.type == EventType.ERROR:
                break
            name = (ev.payload or {}).get("channel_name", "")
            if name:
                found.append((idx, name))
        return found

    async def _resolve_channel(self) -> int:
        if self.cfg.channel_index is not None:
            return self.cfg.channel_index
        wanted = self.cfg.channel.strip().casefold()
        for idx, name in await self.list_channels():
            if name.strip().casefold() == wanted:
                return idx
        raise SendError(
            f"Kanal {self.cfg.channel!r} ist auf dem Node nicht eingerichtet. "
            "Lege ihn in der MeshCore-App an oder nutze 'nina-warnbot --list-channels'."
        )

    async def send(self, text: str) -> None:
        from meshcore import EventType

        if self.mc is None:
            raise SendError("Nicht verbunden")
        try:
            ev = await self.mc.commands.send_chan_msg(self.channel_idx, text)
        except Exception as exc:  # Verbindungsabbruch o. Ä.
            raise SendError(f"Senden fehlgeschlagen: {exc}") from exc
        if ev is None or ev.type == EventType.ERROR:
            reason = getattr(ev, "payload", None)
            raise SendError(f"Node hat die Nachricht abgelehnt: {reason}")
        log.info("Gesendet: %s", text)

    async def close(self) -> None:
        if self.mc is not None:
            try:
                await self.mc.disconnect()
            except Exception as exc:
                log.debug("Fehler beim Trennen: %s", exc)
            self.mc = None
