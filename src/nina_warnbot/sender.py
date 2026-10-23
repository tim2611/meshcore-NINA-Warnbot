"""Versand in einen MeshCore-Kanal über einen Companion-Node."""

from __future__ import annotations

import asyncio
import fcntl
import glob
import logging
import os
import time

from .config import MeshConfig

log = logging.getLogger(__name__)

# Sekunden, die ein Port bei der Suche nach node_name höchstens antworten darf
PROBE_TIMEOUT = 20.0


class SendError(RuntimeError):
    """Nachricht konnte nicht übergeben werden."""


class FileLock:
    """Exklusiver flock auf einer Datei, die sich mehrere Programme teilen.

    Die Datei wird nur lesend geöffnet und nur angelegt, wenn sie fehlt: Sie
    gehört oft einem anderen Benutzer, und mit fs.protected_regular verbietet
    der Kernel O_CREAT auf fremden Dateien in /tmp. flock braucht kein
    Schreibrecht.
    """

    def __init__(self, path: str, timeout: float):
        self.path = path
        self.timeout = timeout
        self.fd: int | None = None

    @property
    def held(self) -> bool:
        return self.fd is not None

    def _open(self) -> int:
        try:
            return os.open(self.path, os.O_RDONLY)
        except FileNotFoundError:
            pass
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_CREAT, 0o666)
        except FileExistsError:
            return os.open(self.path, os.O_RDONLY)
        try:
            os.fchmod(fd, 0o666)  # andere Benutzer sollen sie auch öffnen können
        except OSError:
            pass
        return fd

    async def acquire(self) -> None:
        if self.fd is not None:
            return
        try:
            fd = self._open()
        except OSError as exc:
            raise SendError(f"Lock-Datei {self.path} nicht nutzbar: {exc}") from exc
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    raise SendError(
                        f"Lock {self.path} nach {self.timeout:.0f} s nicht frei – Node ist belegt"
                    ) from None
                await asyncio.sleep(0.5)
        self.fd = fd
        log.debug("Lock %s erhalten", self.path)

    def release(self) -> None:
        if self.fd is None:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None
        log.debug("Lock %s freigegeben", self.path)


class DryRunSender:
    """Gibt Nachrichten nur im Log aus – zum Testen ohne Funk."""

    keep_connected = True

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

    async def release(self) -> None:
        pass

    async def close(self) -> None:
        pass


class MeshCoreSender:
    """Sendet über einen Companion-Node.

    Ohne lock_file bleibt die Verbindung dauerhaft offen. Mit lock_file teilt
    sich der Bot den Node mit anderen Programmen: Er verbindet sich erst, wenn
    er den Lock hat, und trennt nach dem Senden wieder (release()).
    """

    def __init__(self, cfg: MeshConfig):
        self.cfg = cfg
        self.mc = None
        self.channel_idx: int | None = None
        self.lock = FileLock(cfg.lock_file, cfg.lock_timeout) if cfg.lock_file else None

    @property
    def keep_connected(self) -> bool:
        return self.lock is None

    @property
    def connected(self) -> bool:
        return self.mc is not None

    async def connect(self, resolve_channel: bool = True) -> None:
        if self.lock is not None:
            await self.lock.acquire()
        try:
            await self._connect(resolve_channel)
        except BaseException:
            await self.close()
            raise

    async def _connect(self, resolve_channel: bool) -> None:
        from meshcore import MeshCore  # spät importieren, damit Tests ohne Funk laufen

        c = self.cfg
        if c.connection == "serial":
            mc = await (self._find_serial() if c.node_name else self._open_serial(c.serial_port))
        elif c.connection == "tcp":
            log.info("Verbinde per TCP mit %s:%s", c.host, c.tcp_port)
            mc = await MeshCore.create_tcp(c.host, c.tcp_port)
        else:
            log.info("Verbinde per Bluetooth mit %s", c.ble_address)
            mc = await MeshCore.create_ble(c.ble_address, pin=c.ble_pin or None)
        if mc is None:
            raise SendError("Keine Antwort vom Node – ist es ein Companion-Node mit passender Firmware?")
        self.mc = mc
        if not resolve_channel or self.channel_idx is not None:
            return
        self.channel_idx = await self._resolve_channel()
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

    async def _open_serial(self, port: str):
        from meshcore import MeshCore

        log.info("Verbinde seriell mit %s", port)
        return await MeshCore.create_serial(port, self.cfg.baudrate)

    def _serial_candidates(self) -> list[str]:
        """serial_port zuerst, dann alle anderen seriellen USB-Ports.

        ttyACM-Geräte (natives USB, z. B. Heltec V4) kommen zuletzt, weil das
        Öffnen sie neu starten kann.
        """
        found = sorted(glob.glob("/dev/serial/by-id/*")) or \
            sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*"))
        found.sort(key=lambda p: "ACM" in os.path.realpath(p))
        ports, seen = [], set()
        for port in [self.cfg.serial_port, *found]:
            real = os.path.realpath(port)
            if os.path.exists(port) and real not in seen:
                seen.add(real)
                ports.append(port)
        return ports

    async def _find_serial(self):
        """Nimmt den Port, an dem der Companion node_name antwortet.

        Die Portnummer kann wechseln, und andere Geräte können angeschlossen sein.
        """
        wanted = self.cfg.node_name
        ports = self._serial_candidates()
        for port in ports:
            try:
                mc = await asyncio.wait_for(self._open_serial(port), PROBE_TIMEOUT)
            except Exception as exc:
                log.info("Port %s: keine Verbindung (%s)", port, exc or type(exc).__name__)
                continue
            name = (mc.self_info or {}).get("name") if mc is not None else None
            if name == wanted:
                return mc
            log.info("Port %s: nicht %s (%s)", port, wanted, name or "keine Antwort")
            if mc is not None:
                await mc.disconnect()
        raise SendError(
            f"Node {wanted!r} an keinem seriellen Port gefunden. Geprüft: {', '.join(ports) or 'keiner'}"
        )

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

    async def release(self) -> None:
        """Nach einem Sendedurchgang: im geteilten Betrieb trennen und Lock freigeben."""
        if self.lock is not None:
            await self.close()

    async def close(self) -> None:
        if self.mc is not None:
            try:
                await self.mc.disconnect()
            except Exception as exc:
                log.debug("Fehler beim Trennen: %s", exc)
            self.mc = None
        if self.lock is not None:
            self.lock.release()
