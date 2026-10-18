"""Kommandozeile und Dauerbetrieb."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import signal
import sys

from . import __version__
from .bot import WarnBot
from .config import ConfigError, load_config
from .formatter import render, split_message
from .nina import NinaClient, NinaError
from .sender import DryRunSender, MeshCoreSender, SendError
from .state import State

log = logging.getLogger("nina_warnbot")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nina-warnbot",
        description="Leitet NINA-Warnungen des BBK in einen MeshCore-Kanal weiter.",
    )
    p.add_argument("-c", "--config", default="/etc/nina-warnbot/config.toml",
                   help="Pfad zur Konfiguration (Standard: %(default)s)")
    p.add_argument("--dry-run", action="store_true",
                   help="nichts funken, Nachrichten nur ausgeben")
    p.add_argument("--once", action="store_true",
                   help="nur einen Durchlauf ausführen und beenden")
    p.add_argument("--list-channels", action="store_true",
                   help="auf dem Node eingerichtete Kanäle anzeigen")
    p.add_argument("--test-message", metavar="TEXT", nargs="?", const="",
                   help="eine Testnachricht in den Kanal senden")
    p.add_argument("--send", metavar="TEXT",
                   help="einen beliebigen Text ohne Präfix in den Kanal senden, z. B. eine Ankündigung")
    p.add_argument("--preview", action="store_true",
                   help="aktuelle Warnungen abrufen und formatiert anzeigen, ohne Zustand zu ändern")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


async def _preview(cfg, client: NinaClient) -> int:
    bot = WarnBot(cfg, client, DryRunSender(), State("/dev/null"))
    alerts = await bot.fetch_all()
    if not alerts:
        print("Keine aktuellen Warnungen.")
    from .filters import passes
    for a in alerts.values():
        a = await client.enrich(a)
        mark = "→ würde gesendet" if passes(a, cfg.filter) else "– gefiltert"
        print(f"\n{a.id} [{a.severity}] {mark}")
        for part in render(a, cfg.message, cfg.nina.region_label):
            print(f"  {part}  ({len(part.encode('utf-8'))} B)")
    return 0


async def _send_text(cfg, sender, text: str) -> None:
    """Sendet einen Text, bei Bedarf in Teilen – unter dem Lock, falls eingestellt."""
    parts = split_message(text, cfg.message.max_bytes, cfg.message.max_parts)
    await sender.connect()
    try:
        for i, part in enumerate(parts):
            if i:
                await asyncio.sleep(cfg.mesh.send_delay)
            await sender.send(part)
    finally:
        await sender.close()


async def _run(args) -> int:
    cfg = load_config(args.config)
    logging.getLogger().setLevel(cfg.bot.log_level.upper())
    if args.dry_run:
        cfg.bot.dry_run = True

    client = NinaClient(cfg.nina.base_url, cfg.nina.timeout, cfg.nina.user_agent)
    if args.preview:
        return await _preview(cfg, client)

    sender = DryRunSender() if cfg.bot.dry_run else MeshCoreSender(cfg.mesh)

    if args.list_channels:
        await sender.connect(resolve_channel=False)
        for idx, name in await sender.list_channels():
            print(f"{idx}: {name}")
        await sender.close()
        return 0

    if args.test_message is not None:
        prefix = f"{cfg.message.prefix} " if cfg.message.prefix else ""
        text = f"{prefix}Test: {args.test_message or 'Warnbot-Testnachricht, bitte ignorieren'}"
        await _send_text(cfg, sender, text)
        return 0

    if args.send is not None:
        if not args.send.strip():
            log.error("--send braucht einen Text")
            return 2
        await _send_text(cfg, sender, args.send.strip())
        return 0

    state = State(cfg.bot.state_file)
    state.load()
    first_run_silent = not state.existed and not cfg.bot.announce_existing_on_start
    if first_run_silent:
        log.info("Erster Start: vorhandene Warnungen werden gemerkt, aber nicht gesendet")

    bot = WarnBot(cfg, client, sender, state)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    backoff = 30
    try:
        while not stop.is_set():
            try:
                # Dauerverbindung früh aufbauen, damit Fehler sofort auffallen.
                # Im geteilten Betrieb verbindet der Bot erst, wenn er sendet.
                if sender.keep_connected and not first_run_silent and not sender.connected:
                    await sender.connect()
                await bot.run_cycle(silent=first_run_silent)
                first_run_silent = False
                backoff = 30
                wait = cfg.nina.poll_interval + random.uniform(0, 10)
            except NinaError as exc:
                log.warning("%s – neuer Versuch später", exc)
                wait = cfg.nina.poll_interval
            except SendError as exc:
                log.error("%s – Verbindung wird neu aufgebaut in %d s", exc, backoff)
                await sender.close()
                wait = backoff
                backoff = min(backoff * 2, 600)
            if args.once:
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=wait)
            except asyncio.TimeoutError:
                pass
    finally:
        await sender.close()
        state.save()
    log.info("Beendet")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        return asyncio.run(_run(args))
    except ConfigError as exc:
        log.error("Konfiguration: %s", exc)
        return 2
    except (SendError, NinaError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
