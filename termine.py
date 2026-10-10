#!/usr/bin/env python3
"""
Terminsuche Bürgerbüro Magdeburg
================================
Läuft alle paar Minuten auf GitHub (gesteuert von .github/workflows/termine.yml) und sieht im
Online-Terminportal der Stadt nach, ob für das gewählte Anliegen ein Termin frei ist. Erscheint ein
neuer freier Termin, kommt eine Nachricht über einen eigenen ntfy-Kanal aufs Handy.

Was gesucht wird, legst du beim manuellen Start fest (Actions → Terminsuche → Run workflow):
  anliegen   Teil des Namens, z. B. "Reisepass" oder "Anmeldung"; "aus" beendet die Suche
  standort   Teil des Standortnamens, z. B. "Nord"; leer = alle Standorte
Die Auswahl bleibt gespeichert, bis du sie änderst.

Das Programm bucht nie selbst. Buchen musst du im Portal.

Geheimnis (GitHub-Secret): NTFY_TERMINE = Name des ntfy-Kanals für die Terminsuche.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path

import requests

BASIS = "https://terminvergabe.magdeburg.de"
PORTAL = BASIS + "/select2?md=2"
MDT = 235                                          # Funktionseinheit "BürgerBüro"
# Alle Anliegen des BürgerBüros, in der Reihenfolge des Portals (aus der Adresse von Schritt 3)
ANLIEGEN_IDS = [2580, 2579, 2582, 2578, 2601, 2593, 2595, 2598, 2597, 2609, 2586, 2606, 2587, 2591, 2592,
                2590, 2589, 2588, 2607, 2827, 2594, 2596, 2599, 2608, 2585, 2859, 2860, 2604, 2605, 2583, 2584]
ZUSTAND_DATEI = Path(os.environ.get("TERMINE_DATEI", "termine.json"))
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh/")
NTFY_THEMA = os.environ.get("NTFY_TERMINE", "").strip()
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
           "Accept-Language": "de-DE,de;q=0.9"}
DATUM = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
UHRZEIT = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
KEINE = re.compile(r"kein(e|en)?\s+(freien?\s+)?(Termin|Zeiten)\w*\s+(mehr\s+)?verfügbar", re.IGNORECASE)


def jetzt():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- HTML lesen
class Leser(HTMLParser):
    """Sammelt Textstücke, Formulare und Formularfelder einer Seite, jeweils mit ihrer Position."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.texte, self.formulare, self.felder, self.links = [], [], [], []
        self.form = None
        self.knopf = None
        self.link = None
        self.ueberspringen = 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style"):
            self.ueberspringen += 1
        elif tag == "form":
            self.form = {"action": a.get("action", ""), "method": a.get("method", "get").lower(),
                         "felder": [], "knoepfe": [], "pos": len(self.texte)}
            self.formulare.append(self.form)
        elif tag in ("input", "select", "textarea"):
            name, typ = a.get("name", ""), a.get("type", "text").lower()
            eintrag = {"name": name, "value": a.get("value", ""), "typ": typ, "pos": len(self.texte),
                       "id": a.get("id", ""), "aria": a.get("aria-label", "") or a.get("title", "")}
            self.felder.append(eintrag)
            if self.form is not None and name:
                (self.form["knoepfe"] if typ == "submit" else self.form["felder"]).append(eintrag)
        elif tag == "button":
            self.knopf = {"name": a.get("name", ""), "value": a.get("value", ""), "text": ""}
            if self.form is not None:
                self.form["knoepfe"].append(self.knopf)
        elif tag == "a" and a.get("href"):
            self.link = {"href": a["href"], "text": ""}
            self.links.append(self.link)

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ueberspringen = max(0, self.ueberspringen - 1)
        elif tag == "form":
            self.form = None
        elif tag == "button":
            self.knopf = None
        elif tag == "a":
            self.link = None

    def handle_data(self, daten):
        if self.ueberspringen:
            return
        text = " ".join(daten.split())
        if text:
            self.texte.append(text)
            if self.knopf is not None:
                self.knopf["text"] = (self.knopf["text"] + " " + text).strip()
            if self.link is not None:
                self.link["text"] = (self.link["text"] + " " + text).strip()


def lies(html):
    leser = Leser()
    leser.feed(html)
    return leser


def seitentext(leser):
    return " ".join(leser.texte)


# ---------------------------------------------------------------- Portal abfragen
def anliegen_liste(sitzung):
    """Namen der Anliegen aus dem Portal. Der Name steht als letztes längeres Textstück vor dem Zählerfeld."""
    leser = lies(sitzung.get(PORTAL, timeout=30).text)
    namen = {}
    for feld in leser.felder:
        treffer = re.fullmatch(r"cnc-(\d+)", feld["name"] or feld["id"])
        if not treffer or int(treffer.group(1)) not in ANLIEGEN_IDS:
            continue
        name = re.sub(r"^Anzahl\s*:?\s*", "", feld["aria"]).strip() if len(feld["aria"]) > 6 else ""
        if not name:
            for text in reversed(leser.texte[:feld["pos"]]):
                if len(re.findall(r"[A-Za-zÄÖÜäöüß]", text)) >= 5 and text.lower() not in ("information", "hinweis"):
                    name = text
                    break
        namen[int(treffer.group(1))] = name
    return [{"id": i, "name": namen.get(i, "")} for i in ANLIEGEN_IDS]


def waehle_anliegen(eingabe, liste):
    """Findet das Anliegen zu einer Eingabe wie "Reisepass" oder "2580". Gibt (Anliegen, weitere Treffer) zurück."""
    eingabe = eingabe.strip()
    if eingabe.isdigit() and int(eingabe) in ANLIEGEN_IDS:
        return next(a for a in liste if a["id"] == int(eingabe)), []
    treffer = [a for a in liste if a["name"] and eingabe.lower() in a["name"].lower()]
    return (treffer[0], treffer[1:]) if treffer else (None, [])


def standorte(sitzung, anliegen_id):
    """Öffnet die Standortauswahl und gibt je Standort (Name, Formular-Absendedaten) zurück."""
    parameter = {"mdt": MDT, "select_cnc": 1, **{f"cnc-{i}": int(i == anliegen_id) for i in ANLIEGEN_IDS}}
    antwort = sitzung.get(BASIS + "/location", params=parameter, timeout=30)
    leser = lies(antwort.text)
    nummern = {}
    for i, text in enumerate(leser.texte):              # Überschriften wie "1: BürgerBüro Mitte", auch zerteilt
        treffer = re.match(r"^(\d+):\s*(.*)$", text)
        if treffer:
            name = treffer.group(2).strip() or (leser.texte[i + 1] if i + 1 < len(leser.texte) else "")
            if name and treffer.group(1) not in nummern:
                nummern[treffer.group(1)] = name
    ergebnis = []
    for form in leser.formulare:
        knoepfe = [k for k in form["knoepfe"] if "weiter" in (k.get("text") or k.get("value") or "").lower()] or form["knoepfe"]
        for knopf in knoepfe:
            beschriftung = (knopf.get("text") or knopf.get("value") or "")
            nummer = re.search(r"(\d+)\s*$", beschriftung)
            name = nummern.get(nummer.group(1)) if nummer else None
            if not name:
                continue
            daten = {f["name"]: f["value"] for f in form["felder"] if f["name"]}
            if knopf.get("name"):
                daten[knopf["name"]] = knopf.get("value", "")
            ziel = requests.compat.urljoin(antwort.url, form["action"] or antwort.url)
            ergebnis.append({"name": name, "ziel": ziel, "methode": form["method"], "daten": daten})
    for link in leser.links:                            # falls "Weiter mit 1" ein Link statt eines Formulars ist
        nummer = re.search(r"weiter\s+mit\s+(\d+)", link["text"], re.IGNORECASE)
        if nummer and nummern.get(nummer.group(1)) and not any(o["name"] == nummern[nummer.group(1)] for o in ergebnis):
            ergebnis.append({"name": nummern[nummer.group(1)], "ziel": requests.compat.urljoin(antwort.url, link["href"]),
                             "methode": "get", "daten": {}})
    return ergebnis, seitentext(leser)


def termine_an(sitzung, standort):
    """Fragt einen Standort ab. Ergebnis: ("keine" | "frei" | "unklar", [{tag, zeiten}], Seitentext)."""
    if standort["methode"] == "post":
        antwort = sitzung.post(standort["ziel"], data=standort["daten"], timeout=30)
    else:
        antwort = sitzung.get(standort["ziel"], params=standort["daten"], timeout=30)
    text = seitentext(lies(antwort.text))
    if KEINE.search(text):
        return "keine", [], text
    tage, aktuell = [], None
    for stueck in re.split(r"(?=\b\d{1,2}\.\d{1,2}\.\d{4}\b)", text):
        datum = DATUM.match(stueck)
        if datum:
            aktuell = {"tag": f"{int(datum.group(1)):02d}.{int(datum.group(2)):02d}.{datum.group(3)}", "zeiten": []}
            tage.append(aktuell)
        if aktuell is not None:
            for h, m in UHRZEIT.findall(stueck):
                zeit = f"{int(h):02d}:{m}"
                if zeit not in aktuell["zeiten"]:
                    aktuell["zeiten"].append(zeit)
    if tage and "Terminvorschl" in text:
        return "frei", tage, text
    return "unklar", tage, text


# ---------------------------------------------------------------- Handy
def sende(titel, text, dringlichkeit=4):
    print(f"MELDUNG: {titel} | {text.splitlines()[0] if text else ''}")
    if not NTFY_THEMA:
        print("  Kein NTFY_TERMINE hinterlegt, deshalb geht nichts aufs Handy.")
        return
    try:
        requests.post(NTFY_SERVER, json={"topic": NTFY_THEMA, "title": titel, "message": text,
                                          "priority": dringlichkeit, "click": PORTAL}, timeout=15).raise_for_status()
    except Exception as fehler:
        print(f"  Nachricht konnte nicht gesendet werden: {fehler}")


def kurz_tag(tag):
    """14.10.2026 → Di 14.10."""
    t, m, j = (int(x) for x in tag.split("."))
    return ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][datetime(j, m, t).weekday()] + f" {t:02d}.{m:02d}."


# ---------------------------------------------------------------- Ablauf
def main():
    try:
        zustand = json.loads(ZUSTAND_DATEI.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        zustand = {}
    sitzung = requests.Session()
    sitzung.headers.update(BROWSER)
    eingabe_anliegen = os.environ.get("ANLIEGEN", "").strip()
    eingabe_standort = os.environ.get("STANDORT", "").strip()

    try:
        if eingabe_anliegen.lower() == "aus":
            zustand.update(auswahl=None, status="aus", termine=[], bekannt=[], fehler=None)
            sende("⏹ Terminsuche beendet", "Es wird nicht mehr nach Terminen gesucht.", 3)
        elif eingabe_anliegen:
            liste = anliegen_liste(sitzung)
            zustand["anliegen_liste"] = liste
            gewaehlt, weitere = waehle_anliegen(eingabe_anliegen, liste)
            if not gewaehlt:
                bekannte = ", ".join(a["name"] for a in liste if a["name"]) or "keine Namen lesbar"
                zustand.update(status="fehler", fehler=f'Kein Anliegen passt zu "{eingabe_anliegen}".')
                sende("⚠️ Anliegen nicht gefunden",
                      f'Zu "{eingabe_anliegen}" passt kein Anliegen. Verfügbar: {bekannte}', 4)
            else:
                zustand.update(auswahl={"id": gewaehlt["id"], "name": gewaehlt["name"] or f"Anliegen {gewaehlt['id']}",
                                        "standort": eingabe_standort, "seit": jetzt()},
                               termine=[], bekannt=[], fehler=None)
                text = f"Gesucht wird: {zustand['auswahl']['name']}\nStandort: {eingabe_standort or 'alle'}"
                if weitere:
                    text += "\nAndere Treffer: " + "; ".join(a["name"] for a in weitere)
                sende("\U0001F50E Terminsuche gestartet", text, 3)

        auswahl = zustand.get("auswahl")
        if auswahl:
            orte, ortstext = standorte(sitzung, auswahl["id"])
            if auswahl["standort"]:
                orte = [o for o in orte if auswahl["standort"].lower() in o["name"].lower()]
            if not orte:
                raise RuntimeError("Keine Standorte gefunden. Seite: " + ortstext[:300])
            ergebnisse, unklar = [], []
            for ort in orte:
                art, tage, text = termine_an(sitzung, ort)
                if art == "frei":
                    ergebnisse += [{"standort": ort["name"], **t} for t in tage]
                elif art == "unklar":
                    unklar.append(f"{ort['name']}: {text[:200]}")
            zustand["termine"] = ergebnisse
            zustand["status"] = "frei" if ergebnisse else ("unklar" if unklar else "keine")
            zustand["fehler"] = "Seite nicht eindeutig lesbar: " + " | ".join(unklar) if unklar else None
            zustand["standorte"] = [o["name"] for o in orte]

            schluessel = {f"{e['standort']}|{e['tag']}|{z}" for e in ergebnisse for z in (e["zeiten"] or ["?"])}
            neu = schluessel - set(zustand.get("bekannt", []))
            if neu:
                zeilen = []
                for e in ergebnisse:
                    zeiten = [z for z in e["zeiten"] if f"{e['standort']}|{e['tag']}|{z}" in neu] or e["zeiten"]
                    if any(f"{e['standort']}|{e['tag']}|{z}" in neu for z in (e["zeiten"] or ["?"])):
                        zeilen.append(f"{e['standort']}: {kurz_tag(e['tag'])} " + ", ".join(zeiten[:6])
                                      + (" …" if len(zeiten) > 6 else ""))
                sende(f"\U0001F4C5 Freier Termin: {auswahl['name']}",
                      "\n".join(zeilen[:8]) + "\n\nSchnell sein: Tippe hier und buche im Portal.", 5)
            zustand["bekannt"] = sorted(schluessel)
    except Exception as fehler:
        zustand.update(status="fehler", fehler=str(fehler)[:400])
        print(f"Fehler: {fehler}")

    zustand["zuletzt_geprueft"] = jetzt()
    ZUSTAND_DATEI.write_text(json.dumps(zustand, ensure_ascii=False), encoding="utf-8")
    print(f"Fertig: Status {zustand.get('status')}, {len(zustand.get('termine') or [])} Tage mit freien Terminen.")


if __name__ == "__main__":
    main()
