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
- Testlauf ohne Funk (`--dry-run`) und Vorschau (`--preview`)
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
sudo /opt/nina-warnbot/venv/bin/pip install git+https://<dein-git-server>/<user>/Meshcore-NINA-Warnbot.git

sudo mkdir -p /etc/nina-warnbot
sudo cp config.example.toml /etc/nina-warnbot/config.toml
sudoedit /etc/nina-warnbot/config.toml
```

### Regionalschlüssel finden

Die ersten fünf Stellen des Amtlichen Regionalschlüssels bezeichnen den
Landkreis. Sie stehen z. B. in der Gemeindeliste des Statistischen
Bundesamts. Beispiele: `07233` Vulkaneifel, `07231` Bernkastel-Wittlich,
`07232` Eifelkreis Bitburg-Prüm. Der Bot füllt automatisch mit Nullen auf.

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
| `bot.announce_updates` | `never`, `severity` oder `always` |

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

## Lizenz

MIT – siehe [LICENSE](LICENSE).
