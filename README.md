# Trading Bot

Automatisierter Trading Bot fuer **Gold, Silber, Nasdaq 100 (NQ) und S&P 500 (ES)**.
Aktueller Stand: **Backtest und Paper Trading mit Spielgeld**. Echtes Geld ist absichtlich noch nicht angebunden.

## Was der Bot kann

- **Marktdaten** kostenlos von Yahoo Finance (5-Minuten-Kerzen, Cache in `data/`).
- **Strategiebibliothek**: EMA-Cross (Trend), Breakout (Ausbruch), RSI-Reversion, Ausbruch zur US-Eroeffnung (15:30 Uhr), VWAP-Trend, Breakout nur in Trendphasen und RSI nur in Seitwaertsphasen (ADX-Marktphasen-Filter), jeweils mit mehreren Parametern.
- **Automatische SL/TP**: Stop-Loss = 1,5 x ATR, Take-Profit = 3 x Stop-Abstand (**RR 1:3**). Gesetzt vom Risk Manager, nicht von der Strategie.
- **Risk Manager**: max. 1 % Risiko pro Trade, max. 3 % Tagesverlust (danach Pause bis zum naechsten Tag), max. offene Positionen.
- **Lernt aus Verlusttrades**: erkennt Uhrzeiten und Volatilitaetsphasen, in denen eine Strategie systematisch verliert, und sperrt sie.
- **Sucht sich selbst eine Strategie**: Walk-Forward-Test. Auf alten Daten waehlen, auf ungesehenen Daten pruefen. Eine Strategie muss ausserdem in beiden Haelften des Trainingsfensters im Plus sein (Schutz vor Zufallstreffern). Nur was out-of-sample besteht, kommt ins Paper Trading.
- **Trade-Journal** in SQLite (`data/journal.sqlite`).

## Installation (alter Laptop, Linux oder Windows)

```bash
git clone <repo-url> && cd trading-bot
python3 -m venv .venv && source .venv/bin/activate   # Windows: python -m venv .venv; .venv\Scripts\activate
pip install -e . pytest
cp config/config.example.yaml config/config.yaml
pytest                      # alles gruen?
```

## Benutzung

```bash
tradingbot backtest         # alle Strategien auf allen Maerkten testen
tradingbot select           # beste Strategie je Markt per Walk-Forward waehlen -> data/selection.json
tradingbot paper            # Paper Trading starten (prueft alle 5 Minuten)
tradingbot backtest --synthetic   # ohne Internet, mit Zufallsdaten
tradingbot select --timeframe 1h  # 1-Stunden-Kerzen, 2 Jahre Historie (aussagekraeftiger)
tradingbot select --timeframe 15m # 15-Minuten-Kerzen, 60 Tage
tradingbot select --timeframe 1d --exit trailing   # Trendfolge: Tageskerzen, nachgezogener Stop, kein festes TP
```

**Ausstiegs-Modi** (`--exit`):
- `fixed` (Standard): SL = 1,5 x ATR, TP = 3 x SL (RR 1:3).
- `trailing`: Stop startet bei 3 x ATR und wird mit dem Kurs nachgezogen, kein festes Ziel. Klassische Trendfolge:
  wenige grosse Gewinner, viele kleine Verluste.

`tradingbot paper` nutzt automatisch dieselbe Kerzengroesse wie das letzte `select`.

### Krypto (Bitcoin, Ethereum, Solana)

```bash
tradingbot backtest --config config/crypto.yaml
tradingbot select --config config/crypto.yaml --timeframe 1h
./paper-crypto.sh             # Paper-Testlauf mit 20 USD Spielgeld
./dashboard-crypto.sh         # Dashboard dazu auf http://localhost:8051
```

Krypto laeuft rund um die Uhr, man kann Bruchteile kaufen (z. B. 0.0008 BTC), und die Gebuehren sind prozentual
(0,1 % pro Seite in `config/crypto.yaml`). Der Krypto-Testlauf speichert seinen Stand getrennt in `data/crypto/`,
kann also gleichzeitig mit dem Futures-Testlauf laufen.

### Top-Trader kopieren (Hyperliquid, nur Paper Trading)

Der Copy-Bot haelt eine Dauerverbindung (WebSocket) zu Hyperliquid: Trades der Trader loesen sofort eine Runde aus,
und jeder Preis-Tick prueft Stop und Ziel. Ohne das Paket `websocket-client` faellt er auf eine Abfrage pro Minute zurueck.

Auf Hyperliquid sind Positionen und Trades aller Konten oeffentlich. Der Bot nutzt das so:

```bash
./copy-trading.sh scan        # beste Trader der letzten 30 Tage finden und bewerten
./copy-trading.sh test        # ehrlicher Test: Auswahl mit alten Daten, Kopieren mit 60 s Verzoegerung danach
./copy-trading.sh watch       # Paper-Copy-Trading mit 20 $ Spielgeld
./copy-trading.sh dashboard   # Dashboard dazu auf http://localhost:8052 (Option --no-browser)
./copy-trading.sh status      # Kontostand, offene Positionen, letzte Trades im Terminal
```

- **Vorauswahl:** Konto >= 50.000 $, Gewinn im letzten Monat und insgesamt, kein Hochfrequenz-Handel
  (Monatsvolumen hoechstens 150x Kontowert, sonst ist es meist ein Market Maker, den man nicht kopieren kann).
- **Bewertung:** Gewinn-Tage, Gewinn-Wochen, t-Wert (> 2 = eher kein Zufall), Anzahl neuer Positionen pro Tag.
- **Kopieren:** Nur neue Positionen (nicht Nachkaeufe). Unser Risk Manager setzt SL/TP mit RR 1:3.
  Schliesst der Trader, steigen wir auch aus. Nur Perpetuals; Spot- und HIP-3-Maerkte sowie Konten mit viel
  Spot-Volumen (meist Absicherungs-Geschaefte) werden ausgelassen.
- **Schutz:** Tagesverlust-Limit (Kill Switch) und max. offene Positionen aus `config/hyperliquid.yaml`.
- **Neuauswahl:** `watch` waehlt die Trader alle 24 Stunden neu aus (`--rescan-hours 0` schaltet das ab).
  Eine laufende Kopie wird trotzdem zu Ende gefuehrt.
- **Ereignisse:** Jede Kopie, jeder Ausstieg und jedes Ueberspringen steht in `data/copy/traders/events.jsonl`
  und im Dashboard.
- **Grenzen:** Das Leaderboard zeigt nur heute existierende Konten (Survivorship Bias). Hyperliquid hat eine
  Mindestorder von 10 $. Mit 20 $ geht das nur mit Hebel; der Paper-Bot ueberspringt kleinere Orders.

### Dauer-Lernschleife (Futures: ICT/TJR, Krypto: Momentum/Volumen/RSI)

Der Bot laedt alle paar Stunden neue Kerzen, haengt sie an ein wachsendes Archiv (`data/<bot>/archive/`) an und
testet alle Strategie-Varianten neu, nur auf Daten, die er vorher nicht gesehen hat (Walk-Forward), und gegen
Zufalls-Einstiege. Eine Strategie gilt erst als bestanden, wenn sie mindestens 30 Trades mit Gewinn macht und den
Zufall in 90 % der Laeufe schlaegt. Echtes Geld handelt die Schleife nie.

```bash
tradingbot learn --family ict                                          # Futures (Gold, Silber, NQ, ES; 5m)
tradingbot learn --family momentum --config config/crypto-learn.yaml   # Krypto (9 Coins, 1h, 1000 $ Spielgeld)
```

Zusaetzlich sucht jede Runde **neue Varianten** (zufaellige Parameter, bereits getestete werden uebersprungen).
Ausgewaehlt wird nur auf den ersten 75 % der Daten; die letzten 25 % dienen als Pruefung. Je mehr Varianten schon
geprueft wurden, desto hoeher die Huerde gegen Zufall (90 %, 95 %, ... bis 99,5 %). Ein Fund wird gemeldet
(Log, Telegram, Obsidian), aber nie automatisch gehandelt. Ueber den Knopf laufen die Schleifen mit 3 Minuten Pause
und niedriger Prozess-Prioritaet, je Schleife auf der Haelfte der Prozessorkerne und mit 120 neuen Varianten pro Runde.

Optional: `OBSIDIAN_LOGBOOK=/Pfad/zur/Logbuch.md` in `.env` schreibt wichtige Ereignisse (Copy-Trades, Funde,
Zwischenstaende der Suche) automatisch oben ins Obsidian-Logbuch.

Im Dashboard (und in der Mac-App) laufen beide auch ueber den Start-Knopf in den Reitern Futures/Krypto;
die Ergebnisse stehen im Reiter **Lernen**.

### Mac-App mit Start/Pause-Knopf

```bash
./macapp/build.sh        # einmalig, baut "Trading Bot.app" im Ordner trading-bot
open "Trading Bot.app"   # oder doppelklicken / ins Dock ziehen
```

Die App zeigt das Dashboard in einem eigenen Fenster. Oben gibt es den Knopf **Bot starten / Pausieren**.
Unter **Was der Bot gerade macht** siehst du live, welchen Trader er gerade prueft, was die Trader halten,
wann die naechste Runde kommt und das Protokoll. Der Bot laeuft im Hintergrund weiter, auch wenn das Fenster zu ist;
Pausieren beendet ihn sauber, Konto und Positionen bleiben gespeichert. Zuklappen des Macs pausiert trotzdem.
Den Knopf gibt es auch im Browser-Dashboard (`./copy-trading.sh dashboard`).

### Meldungen aufs Handy (Telegram, optional)

1. In Telegram `@BotFather` anschreiben, `/newbot`, Namen vergeben. Du bekommst einen Token.
2. Deinem neuen Bot eine Nachricht schicken, dann `https://api.telegram.org/bot<TOKEN>/getUpdates` im Browser
   oeffnen und dort bei `"chat":{"id": ...}` die Zahl ablesen.
3. Im Ordner `trading-bot` eine Datei `.env` anlegen (sie wird nie hochgeladen):

```
TELEGRAM_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=987654321
```

Danach meldet der Copy-Bot jede Kopie, jeden Ausstieg und den Kill Switch aufs Handy. Ohne `.env` passiert nichts.

### Live-Dashboard

In einem zweiten Terminalfenster (der Paper-Bot muss laufen):

```bash
tradingbot dashboard
```

Oeffnet `http://localhost:8050` im Browser und aktualisiert sich alle 5 Sekunden. Laeuft nur lokal auf deinem Rechner,
liest nur Dateien und oeffentliche Hyperliquid-Daten und kann selbst keine Orders ausloesen.

- **Uebersicht:** Konto, Kontoverlauf, offene Positionen mit unrealisiertem Gewinn, Ereignisse.
- **Charts:** Live-Chart von Hyperliquid (auch ohne laufenden Bot) mit Einstieg, Ausstieg, SL und TP, Coin und Kerzengroesse waehlbar.
  Dazu die Maerkte des Bots und die Coins der kopierten Trader.
- **Trades:** alle abgeschlossenen Trades mit Filtern und CSV-Export.
- **Statistik:** Drawdown, bester und schlechtester Trade, Verlustserie, Verteilung in R, Gewinn pro Tag,
  Auswertung nach Markt, Ausstieg und Trader.
- **Copy-Trading:** ausgewaehlte Trader, ihre aktuellen Positionen, ihr Abschneiden im Copy-Test und das letzte Walk-Forward-Ergebnis.
  Das Dashboard warnt, wenn ein Trader im Test gar nicht vorkam.

Oben rechts neben dem Titel laesst sich zwischen Futures, Krypto und Copy-Trading wechseln, sobald dafuer Daten vorliegen.

### Paper-Testlauf (Technik pruefen, ohne bestandene Strategie)

```bash
./paper-test.sh        # Kurzform fuer:
# caffeinate -i tradingbot paper --strategy "trend_breakout(lookback=20, adx_min=25)" --symbols GC=F NQ=F --timeframe 5m
```

- `caffeinate -i` (nur Mac) verhindert den Ruhezustand, solange der Bot laeuft. Ladekabel dranlassen.
- Kontostand und offene Positionen stehen in `data/paper_state.json` und bleiben nach einem Neustart erhalten.
- Jeder geschlossene Trade landet in `data/paper_trades.csv`.
- Beenden mit `Ctrl + C`. Komplett neu starten: `rm data/paper_state.json data/paper_trades.csv`.

Damit der Laptop dauerhaft laeuft: Energiesparmodus und Ruhezustand ausschalten, Deckel-Schliessen auf "nichts tun" stellen,
und `tradingbot paper` z. B. in `tmux` oder als systemd-Dienst starten.

## Ehrliche Hinweise

- **70 % Trefferquote und RR 1:3 gleichzeitig** waere ein Erwartungswert von +1,8 R pro Trade. Das schafft praktisch keine Strategie dauerhaft.
  Bei RR 1:3 ist man schon ab **25 % Trefferquote** im Plus (vor Gebuehren), realistisch sind 30 bis 45 %.
  Der Bot optimiert deshalb auf **Erwartungswert und Profit Factor**, nicht auf die Trefferquote. `min_win_rate` ist in der Config einstellbar.
- Wenn man so lange Strategien sucht, bis eine im Backtest 70 % hat, findet man fast immer eine, die nur Zufall auswendig gelernt hat.
  Darum zaehlen nur Out-of-Sample-Ergebnisse.
- **20 EUR reichen nicht fuer NQ/ES/Gold/Silber-Futures.** Selbst Micro-Kontrakte (MNQ, MES, MGC, SIL) brauchen Hunderte bis
  Tausende Euro Margin, und ein einziger Stop-Loss ist oft groesser als 20 EUR. Der Risk Manager lehnt Trades ab, die zu gross fuers Konto sind.
- Ergebnisse mit `--synthetic` sind bedeutungslos, sie testen nur, ob der Code funktioniert.

## Projektstruktur

```
src/tradingbot/
  config.py              Konfiguration laden
  data/feed.py           Marktdaten (Yahoo) + synthetische Testdaten
  strategies/            Strategien (nur Einstiegssignale)
  risk/manager.py        SL/TP, Positionsgroesse, Tageslimit
  backtest/              Backtester + Kennzahlen
  learning/              Journal, Verlust-Filter, Walk-Forward-Auswahl
  execution/             Broker-Schnittstelle + Paper Broker
  bot.py                 Paper-Trading-Schleife
  dashboard/             Live-Dashboard (lokaler Webserver + Seite)
  traders/               Hyperliquid: Leaderboard, Trader-Bewertung, Copy-Test, Paper-Copy-Trading
  cli.py                 Kommandozeile
config/config.example.yaml
tests/
docs/ROADMAP.md
```
