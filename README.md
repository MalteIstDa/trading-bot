# Trading Bot

Automatisierter Trading Bot fuer **Gold, Silber, Nasdaq 100 (NQ) und S&P 500 (ES)**.
Aktueller Stand: **Backtest und Paper Trading mit Spielgeld**. Echtes Geld ist absichtlich noch nicht angebunden.

## Was der Bot kann

- **Marktdaten** kostenlos von Yahoo Finance (5-Minuten-Kerzen, Cache in `data/`).
- **Strategiebibliothek**: EMA-Cross (Trend), Breakout (Ausbruch), RSI-Reversion, jeweils mit mehreren Parametern.
- **Automatische SL/TP**: Stop-Loss = 1,5 x ATR, Take-Profit = 3 x Stop-Abstand (**RR 1:3**). Gesetzt vom Risk Manager, nicht von der Strategie.
- **Risk Manager**: max. 1 % Risiko pro Trade, max. 3 % Tagesverlust (danach Pause bis zum naechsten Tag), max. offene Positionen.
- **Lernt aus Verlusttrades**: erkennt Uhrzeiten und Volatilitaetsphasen, in denen eine Strategie systematisch verliert, und sperrt sie.
- **Sucht sich selbst eine Strategie**: Walk-Forward-Test. Auf alten Daten waehlen, auf ungesehenen Daten pruefen. Nur was dort besteht, kommt ins Paper Trading.
- **Trade-Journal** in SQLite (`data/journal.sqlite`).

## Installation (alter Laptop, Linux oder Windows)

```bash
git clone <repo-url> && cd trading-bot
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
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
```

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
  cli.py                 Kommandozeile
config/config.example.yaml
tests/
docs/ROADMAP.md
```
