import asyncio
from datetime import datetime, timezone

import pytest

from nina_warnbot.bot import WarnBot
from nina_warnbot.config import ConfigError, normalize_ars, parse_config
from nina_warnbot.formatter import build_text, split_message, transliterate
from nina_warnbot.nina import NinaError, apply_details, parse_dashboard
from nina_warnbot.sender import DryRunSender, SendError
from nina_warnbot.state import State


def dash_item(aid, headline="Sturmböen", severity="Moderate", msg_type="Alert", h=None):
    return {
        "id": aid,
        "payload": {
            "id": aid,
            "hash": h or f"h-{aid}-{severity}-{msg_type}",
            "data": {"headline": headline, "provider": "X", "severity": severity,
                     "msgType": msg_type, "transKeys": {"event": "BBK-EVC-001"}},
        },
        "i18nTitle": {"de": headline},
        "sent": "2026-10-18T10:00:00+02:00",
    }


DETAILS = {"info": [{"language": "de-DE", "headline": "Sturmböen", "expires": "2026-10-18T18:00:00+02:00",
                     "description": "Es treten Sturmböen auf.", "instruction": "Fenster schließen."}]}


class FakeClient:
    def __init__(self, items):
        self.items = items
        self.fail = False

    async def dashboard(self, ars):
        if self.fail:
            raise NinaError("offline")
        return parse_dashboard(self.items)

    async def enrich(self, alert):
        info = dict(DETAILS["info"][0], headline=alert.headline)
        return apply_details(alert, {"info": [info]})


class FailingSender(DryRunSender):
    async def send(self, text):
        raise SendError("Funk weg")


def make_cfg(**over):
    raw = {
        "nina": {"ars": ["07233"], "region_label": "Vulkaneifel"},
        "mesh": {"channel": "#warnung"},
        "bot": {"state_file": "unused"},
    }
    for sec, vals in over.items():
        raw.setdefault(sec, {}).update(vals)
    return parse_config(raw)


def make_bot(tmp_path, items, sender=None, **over):
    cfg = make_cfg(**over)
    state = State(tmp_path / "state.json")
    state.load()
    bot = WarnBot(cfg, FakeClient(items), sender or DryRunSender(), state)

    async def no_sleep(_):
        pass

    bot._sleep = no_sleep
    return bot


def run(coro):
    return asyncio.run(coro)


# --- Konfiguration -----------------------------------------------------------

def test_normalize_ars():
    assert normalize_ars("07233") == "072330000000"
    assert normalize_ars("072335001045") == "072330000000"
    with pytest.raises(ConfigError):
        normalize_ars("72a")


def test_config_rejects_unknown_key():
    with pytest.raises(ConfigError):
        parse_config({"nina": {"ars": "07233", "tippfehler": 1}, "mesh": {"channel": "x"}})


def test_config_requires_channel():
    with pytest.raises(ConfigError):
        parse_config({"nina": {"ars": "07233"}})


def test_config_poll_interval_minimum():
    with pytest.raises(ConfigError):
        make_cfg(nina={"poll_interval": 10})


# --- Formatierung ------------------------------------------------------------

def test_build_text_contains_essentials():
    cfg = make_cfg()
    alert = apply_details(parse_dashboard([dash_item("dwd.1", severity="Severe")])[0], DETAILS)
    text = build_text(alert, cfg.message, "Vulkaneifel")
    assert text.startswith("⚠️ DWD [HOCH] Sturmböen – Vulkaneifel")
    assert "bis 18.10. 18:00" in text


def test_split_respects_byte_limit():
    long = "Wort " * 80
    parts = split_message(long, 60, 3)
    assert len(parts) == 3
    assert all(len(p.encode()) <= 60 for p in parts)
    assert parts[0].endswith("(1/3)") and parts[-1].endswith("(3/3)")
    assert parts[-1].endswith("... (3/3)")  # ASCII-Text -> ASCII-Auslassung


def test_split_handles_overlong_word_and_umlauts():
    parts = split_message("Ä" * 200, 50, 2)
    assert all(len(p.encode()) <= 50 for p in parts)


def test_transliterate():
    assert transliterate("Sturmböen – Straße ⚠️") == "Sturmboeen - Strasse "


# --- Ablauf ------------------------------------------------------------------

def test_first_run_silent_then_only_new(tmp_path):
    bot = make_bot(tmp_path, [dash_item("mow.1")])
    assert run(bot.run_cycle(silent=True)) == 0
    bot.client.items.append(dash_item("mow.2", headline="Gefahrstoff"))
    assert run(bot.run_cycle()) == 1
    assert "Gefahrstoff" in bot.sender.sent[0]
    assert run(bot.run_cycle()) == 0  # nichts doppelt


def test_filter_min_severity_and_provider(tmp_path):
    items = [dash_item("dwd.1", severity="Minor"), dash_item("pol.1", severity="Extreme")]
    bot = make_bot(tmp_path, items, filter={"providers": ["dwd", "mow"]})
    assert run(bot.run_cycle()) == 0


def test_severity_update_mode(tmp_path):
    bot = make_bot(tmp_path, [dash_item("dwd.1", severity="Moderate")])
    run(bot.run_cycle())
    # gleiche Stufe, anderer Inhalt -> kein erneutes Senden
    bot.client.items = [dash_item("dwd.1", severity="Moderate", h="neu")]
    assert run(bot.run_cycle()) == 0
    # Stufe steigt -> senden
    bot.client.items = [dash_item("dwd.1", severity="Extreme")]
    assert run(bot.run_cycle()) == 1
    assert "[EXTREM]" in bot.sender.sent[-1]


def test_cancel_only_for_announced(tmp_path):
    bot = make_bot(tmp_path, [dash_item("mow.1")])
    run(bot.run_cycle())
    bot.client.items = [dash_item("mow.1", msg_type="Cancel"), dash_item("mow.9", msg_type="Cancel")]
    assert run(bot.run_cycle()) == 1
    assert "Entwarnung" in bot.sender.sent[-1]
    assert run(bot.run_cycle()) == 0


def test_no_cancel_for_silently_recorded(tmp_path):
    bot = make_bot(tmp_path, [dash_item("mow.1")])
    run(bot.run_cycle(silent=True))
    bot.client.items = [dash_item("mow.1", msg_type="Cancel")]
    assert run(bot.run_cycle()) == 0


def test_budget_defers_rest(tmp_path):
    items = [dash_item(f"mow.{i}") for i in range(5)]
    bot = make_bot(tmp_path, items, mesh={"max_messages_per_cycle": 2})
    assert run(bot.run_cycle()) == 2
    assert run(bot.run_cycle()) == 2
    assert run(bot.run_cycle()) == 1


def test_send_failure_keeps_state(tmp_path):
    bot = make_bot(tmp_path, [dash_item("mow.1")], sender=FailingSender())
    with pytest.raises(SendError):
        run(bot.run_cycle())
    assert not (bot.state.get("mow.1") or {}).get("announced")


def test_state_persists(tmp_path):
    bot = make_bot(tmp_path, [dash_item("mow.1")])
    run(bot.run_cycle())
    bot2 = make_bot(tmp_path, [dash_item("mow.1")])
    assert bot2.state.existed
    assert run(bot2.run_cycle()) == 0


def test_all_regions_failing_raises(tmp_path):
    bot = make_bot(tmp_path, [])
    bot.client.fail = True
    with pytest.raises(NinaError):
        run(bot.run_cycle())


def test_parse_dashboard_tolerates_junk():
    alerts = parse_dashboard([{"foo": 1}, "x", dash_item("kat.5")])
    assert [a.id for a in alerts] == ["kat.5"]
    assert alerts[0].provider_label == "KATWARN"
