# Meshcore-NINA-Warnbot

Leitet amtliche Warnungen aus der [NINA-API](https://nina.api.bund.dev) des
Bundesamts für Bevölkerungsschutz und Katastrophenhilfe (BBK) in einen
[MeshCore](https://meshcore.co.uk)-Kanal weiter. So erreichen Warnungen auch
Menschen in Gegenden mit schlechtem Mobilfunk – über LoRa, ohne Internet auf
Empfängerseite.

> **Wichtig:** Dies ist ein privates Community-Projekt und **kein offizieller
> Warnkanal**. Es ersetzt weder Sirenen, Cell Broadcast noch die NINA-App.
> Funk kann ausfallen, Nachrichten können verloren gehen.

## Funktionen

- Ein oder mehrere Landkreise per Regionalschlüssel (ARS)
- Quellen: MoWaS, KATWARN, BIWAPP, Deutscher Wetterdienst, Hochwasserportal, Polizei
- Filter nach Warnstufe, Quelle und Ereigniscode
- Kurze, funktaugliche Nachrichten mit Byte-Grenze und automatischer Teilung
- Wichtigste Warnung zuerst, Sendebudget pro Durchlauf und Pause zwischen
  Nachrichten – schont die Airtime im 868-MHz-Band
- Keine Doppelmeldungen, auch nach Neustart (Zustandsdatei)
- Erster Start sendet keine Altlasten (abschaltbar)
- Optional: Entwarnung bei Aufhebung und Meldung bei steigender Warnstufe
- Verbindung zum Companion-Node per USB, TCP oder Bluetooth
- Optional geteilter Betrieb: Verbindung nur zum Senden, unter einem
  gemeinsamen Lock mit anderen Programmen am selben Node
- Testlauf ohne Funk (`--dry-run`) und Vorschau (`--preview`)
- Eigene Ankündigungen in den Kanal (`--send`)
- systemd-Unit mit Härtung

Beispielnachricht:

```
⚠️ DWD [HOCH] Amtliche Warnung vor Sturmböen – Vulkaneifel, bis 18.10. 18:00
```

## Voraussetzungen

- Linux-Rechner mit Internet, z. B. Raspberry Pi
- Python 3.11 oder neuer
- MeshCore-Node mit **Companion-Firmware** (USB-Serial, WiFi/TCP oder BLE)
- Ein Kanal auf dem Node, in den gesendet wird – am einfachsten ein
  Hashtag-Kanal wie `#warnung`, den alle in der Region abonnieren können

## Installation

```bash
sudo useradd --system --home /var/lib/nina-warnbot --shell /usr/sbin/nologin nina-warnbot
sudo python3 -m venv /opt/nina-warnbot/venv
sudo /opt/nina-warnbot/venv/bin/pip install git+https://github.com/tim2611/meshcore-NINA-Warnbot.git

sudo mkdir -p /etc/nina-warnbot
sudo cp config.example.toml /etc/nina-warnbot/config.toml
sudoedit /etc/nina-warnbot/config.toml
```

### Regionalschlüssel finden

Die ersten fünf Stellen des Amtlichen Regionalschlüssels bezeichnen den
Landkreis. Am schnellsten findet man sie über die
[Gemeindesuche im Statistikportal](https://www.statistikportal.de/de/gemeindeverzeichnis):
einen beliebigen Ort im Kreis eingeben, die ersten fünf Stellen des
angezeigten Gemeindeschlüssels (AGS) übernehmen. Beispiele: `07233`
Vulkaneifel, `07231` Bernkastel-Wittlich, `07232` Eifelkreis Bitburg-Prüm.
Der Bot füllt automatisch mit Nullen auf.

### Erst testen

```bash
# Aktuelle Warnungen abrufen und so anzeigen, wie sie gefunkt würden
/opt/nina-warnbot/venv/bin/nina-warnbot -c /etc/nina-warnbot/config.toml --preview

# Kanäle auf dem Node anzeigen
sudo -u nina-warnbot /opt/nina-warnbot/venv/bin/nina-warnbot -c /etc/nina-warnbot/config.toml --list-channels

# Testnachricht funken
sudo -u nina-warnbot /opt/nina-warnbot/venv/bin/nina-warnbot -c /etc/nina-warnbot/config.toml --test-message
```

Für den seriellen Zugriff muss der Benutzer in der Gruppe `dialout` sein
(`sudo usermod -aG dialout nina-warnbot`); die systemd-Unit erledigt das
für den Dienst selbst.

### Als Dienst starten

```bash
sudo cp systemd/nina-warnbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nina-warnbot
journalctl -u nina-warnbot -f
```

### Im Kanal ankündigen

Wer den Kanal liest, sollte wissen, was dort künftig automatisch erscheint.
Mit `--send` geht ein beliebiger Text ohne „Test:“-Präfix hinaus, mit
derselben Byte-Grenze und demselben Lock wie die Warnungen:

```bash
sudo -u nina-warnbot /opt/nina-warnbot/venv/bin/nina-warnbot -c /etc/nina-warnbot/config.toml \
  --send "ℹ️ Neu: NINA-Warnungen für Vulkaneifel ab Stufe MITTEL kommen hier automatisch. Kein offizieller Warnkanal!"
```

Der Hinweis „Kein offizieller Warnkanal“ ist wichtig, damit sich niemand
allein auf das Mesh verlässt.

### Node mit anderen Programmen teilen

Hängen am selben Companion-Node noch andere Programme, z. B. Skripte mit
`meshcore-cli`, darf immer nur eines die Verbindung offen haben. Dafür gibt es
`mesh.lock_file`:

```toml
[mesh]
lock_file = "/tmp/meshcli.lock"
```

Der Bot verbindet sich dann nur, wenn er etwas zu senden hat. Vorher wartet er
auf einen exklusiven `flock` auf dieser Datei, nach dem Senden trennt er und
gibt den Lock frei. Die anderen Programme müssen dieselbe Datei genauso
sperren, etwa in der Shell mit `flock /tmp/meshcli.lock meshcore-cli …` oder in
Python mit `fcntl.flock`. Ohne `lock_file` hält der Bot die Verbindung
dauerhaft offen.

Liegt die Lock-Datei in `/tmp`, braucht der Dienst ein Drop-in, sonst sieht er
wegen `PrivateTmp=yes` ein eigenes `/tmp`:

```bash
sudo mkdir -p /etc/systemd/system/nina-warnbot.service.d
sudo cp systemd/shared-lock.conf /etc/systemd/system/nina-warnbot.service.d/
sudo systemctl daemon-reload
```

Zu den Rechten: Der Bot öffnet die Datei nur lesend, das genügt für `flock`.
Fehlt sie, legt er sie mit den Rechten `666` an. Die anderen Programme
sollten sie nach Möglichkeit ebenfalls nur lesend öffnen. Viele Distributionen
setzen `fs.protected_regular`, und dann scheitert in `/tmp` jedes Öffnen mit
`O_CREAT` (z. B. Pythons `open(…, "a")`) an einer Datei, die einem anderen
Benutzer gehört.

## Konfiguration

Alle Einstellungen sind in `config.example.toml` kommentiert. Die wichtigsten:

| Einstellung | Bedeutung |
|---|---|
| `nina.ars` | Liste der Landkreise |
| `nina.poll_interval` | Abrufintervall in Sekunden (min. 60) |
| `filter.min_severity` | `Minor`, `Moderate`, `Severe` oder `Extreme` |
| `filter.providers` | Welche Quellen weitergeleitet werden |
| `message.max_bytes` | Maximale Bytes pro Funknachricht |
| `message.transliterate` | Umlaute ausschreiben, spart Bytes |
| `mesh.connection` | `serial`, `tcp` oder `ble` |
| `mesh.channel` | Kanalname auf dem Node, z. B. `#warnung` |
| `mesh.lock_file` | Node mit anderen Programmen teilen (siehe oben) |
| `bot.announce_updates` | `never`, `severity` oder `always` |

Änderungen wirken erst nach einem Neustart des Dienstes:

```bash
sudo systemctl restart nina-warnbot
```

### Warnstufen

In der Konfiguration stehen die englischen Stufen aus der NINA-API, in den
Funknachrichten die deutschen:

| `min_severity` | In der Nachricht | Beispiel beim DWD |
|---|---|---|
| `Minor` | `[GERING]` | Frost, leichter Schneefall |
| `Moderate` | `[MITTEL]` | Sturmböen, Glätte |
| `Severe` | `[HOCH]` | schwere Sturmböen, Unwetter |
| `Extreme` | `[EXTREM]` | Orkan, extremes Unwetter |

`min_severity = "Severe"` leitet also nur Warnungen ab `[HOCH]` weiter.

### Rücksicht aufs Mesh

Im 868-MHz-Band gelten Sendezeitbegrenzungen (Duty Cycle), und jede
Nachricht belegt das Netz für alle. Deshalb:

- `min_severity` lieber zu hoch als zu niedrig ansetzen. Der DWD gibt in
  manchen Lagen sehr viele Warnungen der Stufe `Minor` heraus.
- `max_parts` klein halten. Eine gute Warnung passt in eine Nachricht.
- Mit der Mesh-Community vor Ort absprechen, in welchen Kanal gesendet wird.

## Entwicklung

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Datenquelle

Warnungen: Bundesamt für Bevölkerungsschutz und Katastrophenhilfe (BBK),
NINA-API, dokumentiert von [bund.dev](https://nina.api.bund.dev). Bitte
das Abrufintervall nicht unter 60 Sekunden setzen.

## Entstehung

Dieses Projekt wurde mit Unterstützung von KI erstellt: Code, Tests und
Dokumentation sind gemeinsam mit [Claude](https://claude.ai) von Anthropic
(über Claude Code) entstanden. Commits mit KI-Beteiligung sind mit
`Co-Authored-By: Claude` gekennzeichnet. Betrieben und im echten Mesh
getestet wird der Bot vom Autor.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
