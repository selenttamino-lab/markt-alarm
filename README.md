# Markt-Alarm

Prüft alle paar Minuten auf GitHub die Kurse von S&P-500-Future, Öl und VIX sowie Schlagzeilen zu Fed,
Zöllen, Öl und Börse. Wichtige Schlagzeilen fasst die Claude-API auf Deutsch zusammen und schätzt ein,
ob sie den Index eher belasten oder stützen. Meldungen gehen per ntfy aufs Handy, alles zusammen steht
auf der Website dieses Projekts.

Die Einschätzungen sind Vermutungen einer KI, die nur Schlagzeilen liest. Keine Anlageberatung.

## Was wo steht

| Datei | Inhalt |
|---|---|
| `markt_alarm.py` | das Prüfprogramm, ein Lauf pro Aufruf |
| `einstellungen.json` | Schwellen, Themen, Signalwörter, KI-Modell, Tagesgrenze |
| `website/` | die Website; `daten.json` schreibt das Programm bei jedem Lauf |
| `.github/workflows/markt-alarm.yml` | startet die Prüfung alle 5 Minuten und veröffentlicht die Website |

## Geheimnisse (Settings → Secrets and variables → Actions)

| Name | Wert |
|---|---|
| `NTFY_THEMA` | dein ntfy-Kanal, wie in der ntfy-App abonniert |
| `ANTHROPIC_API_KEY` | Schlüssel für die Claude-API (freiwillig) |

## Einstellungen ändern

`einstellungen.json` auf GitHub öffnen, auf den Stift klicken, ändern, "Commit changes".
Beispiel für eine Kursmarke beim S&P-500-Future: `"kursmarken": {"ES=F": [7600, 8000]}`.

## Anhalten

Im Reiter "Actions" den Ablauf "Markt-Alarm" wählen, oben rechts über das Menü "Disable workflow".
