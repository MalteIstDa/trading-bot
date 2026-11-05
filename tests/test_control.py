import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from tradingbot import control

SLEEPER = [sys.executable, "-c", "import time  # tradingbot test\nwhile True: time.sleep(0.2)"]


def test_start_stop_keeps_data(monkeypatch, tmp_path):
    monkeypatch.setattr(control, "COMMANDS", {"copy": (tmp_path, SLEEPER)})
    assert control.status("copy")["running"] is False
    st = control.start("copy")
    assert st["running"] and st["pid"]
    assert control.start("copy")["pid"] == st["pid"]  # zweiter Klick startet keinen zweiten Bot
    assert control.stop("copy")["running"] is False
    assert "Start" in (tmp_path / "bot.log").read_text()
    assert control.status("unknown")["controllable"] is False


def test_control_endpoint_needs_own_header(monkeypatch, tmp_path):
    from tradingbot.dashboard import server
    monkeypatch.setattr(control, "COMMANDS", {"copy": (tmp_path, SLEEPER)})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/api/control/"

    def post(action, headers):
        req = urllib.request.Request(url + action, data=json.dumps({"bot": "copy"}).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert post("start", {"Origin": "https://evil.example", "X-Trading-Bot": "1"})[0] == 403
        assert post("start", {})[0] == 403
        code, body = post("start", {"X-Trading-Bot": "1"})
        assert code == 200 and body["running"]
        code, body = post("stop", {"X-Trading-Bot": "1"})
        assert code == 200 and not body["running"]
    finally:
        control.stop("copy")
        httpd.shutdown()


def test_state_json_has_no_infinity():
    from tradingbot.dashboard.server import _finite
    import json
    out = json.dumps(_finite({"pf": float("inf"), "w": [{"x": float("nan")}, 1.5]}), allow_nan=False)
    assert out == '{"pf": null, "w": [{"x": null}, 1.5]}'


def test_vault_logbook_inserts_newest_on_top(tmp_path):
    from tradingbot import vault
    f = tmp_path / "Logbuch.md"
    f.write_text("# Trading-Bot – Logbuch\n\nHandnotiz\n")
    assert vault.add("erster", f) and vault.add("zweiter", f)
    text = f.read_text()
    assert text.index("zweiter") < text.index("erster") < text.index("Handnotiz")
    assert not vault.add("x", tmp_path / "fehlt" / "a.md")


def test_report_summarises_session(tmp_path):
    import json
    from tradingbot import report
    (tmp_path / "traders").mkdir()
    (tmp_path / "paper_trades.csv").write_text(
        "time,symbol,side,reason,pnl,r_multiple,equity,trader\n"
        "2026-11-04T10:00:00+00:00,BTC,1,tp,3.0,3.0,23.0,0xa\n"
        "2026-11-04T11:00:00+00:00,ETH,-1,sl,-1.0,-1.0,22.0,0xb\n"
        "2026-11-03T11:00:00+00:00,SOL,1,sl,-1.0,-1.0,21.0,0xb\n")  # vor dem Start: zaehlt nicht
    (tmp_path / "traders" / "events.jsonl").write_text(json.dumps({"time": "2026-11-04T09:30:00+00:00", "type": "copy", "text": "BTC Long"}) + "\n")
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "history.jsonl").write_text("\n".join(json.dumps(h) for h in [
        {"time": "2026-11-04T08:00:00+00:00", "search_tried": 100},
        {"time": "2026-11-04T10:00:00+00:00", "search_tried": 220, "search_best": "x(a=1)", "search_passed": False},
        {"time": "2026-11-04T11:00:00+00:00", "search_tried": 340, "search_best": "x(a=2)", "search_passed": False}]))
    rep = report.build("copy", "Copy-Bot", tmp_path, "2026-11-04T09:00:00+00:00", "2026-11-04T12:00:00+00:00")
    assert rep["duration_s"] == 3 * 3600
    assert rep["trading"]["closed"] == 2 and abs(rep["trading"]["pnl"] - 2.0) < 1e-9 and rep["trading"]["curve"] == [0.0, 3.0, 2.0]
    assert rep["events"]["by_type"] == {"copy": 1}
    assert rep["learning"]["rounds"] == 2 and rep["learning"]["tried_session"] == 240
    path = report.save(rep, tmp_path)
    assert report.latest(tmp_path)[0]["stopped"] == rep["stopped"] and path.exists()


def test_stop_writes_report(tmp_path, monkeypatch):
    import json
    from tradingbot import control
    monkeypatch.setitem(control.COMMANDS, "demo", (tmp_path, SLEEPER))
    st = control.start("demo")
    assert st["running"]
    control.stop("demo")
    reps = list((tmp_path / "reports").glob("*.json"))
    assert len(reps) == 1 and json.loads(reps[0].read_text())["started"] == st["started"]


def test_power_levels_and_endpoint(monkeypatch, tmp_path):
    from tradingbot import power
    from tradingbot.dashboard import server
    f = tmp_path / "power.json"
    monkeypatch.setattr(power, "FILE", f)
    assert power.current()["level"] == "normal"  # ohne Datei
    lv = power.levels()
    assert lv["eco"]["workers"] == 1 and lv["max"]["workers"] >= lv["normal"]["workers"]
    stamp = 0.0
    assert power.set_level("eco")["workers"] == 1 and power.changed_since(stamp)
    try:
        power.set_level("turbo")
    except ValueError:
        pass
    else:
        raise AssertionError
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/api/power"
    try:
        req = urllib.request.Request(url, data=json.dumps({"level": "max"}).encode(), headers={"X-Trading-Bot": "1"})
        with urllib.request.urlopen(req) as r:
            assert json.loads(r.read())["current"]["level"] == "max"
        with urllib.request.urlopen(url) as r:
            assert json.loads(r.read())["current"]["level"] == "max"
        req = urllib.request.Request(url, data=json.dumps({"level": "eco"}).encode())
        try:
            urllib.request.urlopen(req)
            raise AssertionError("ohne Header muss es abgelehnt werden")
        except urllib.error.HTTPError as e:
            assert e.code == 403
    finally:
        httpd.shutdown()


def test_learn_uses_power_each_round(monkeypatch, tmp_path):
    from tradingbot import power
    from tradingbot.learning import research
    monkeypatch.setattr(power, "FILE", tmp_path / "power.json")
    power.set_level("eco")
    seen = []
    monkeypatch.setattr(research, "update_archive", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(research.log, "info", lambda msg, *a: seen.append(msg % a))
    from tradingbot.config import load_config
    research.run(load_config(), "ict", tmp_path, rounds=1, use_power=True)
    assert any("Sparsam" in x for x in seen)


def test_report_counts_search_restart(tmp_path):
    import json
    from tradingbot import report
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "history.jsonl").write_text("\n".join(json.dumps(h) for h in [
        {"time": "2026-11-04T08:00:00+00:00", "search_tried": 8000},
        {"time": "2026-11-04T10:00:00+00:00", "search_tried": 40},   # neue Suche (1h statt 5m)
        {"time": "2026-11-04T11:00:00+00:00", "search_tried": 120}]))
    rep = report.build("crypto", "Krypto", tmp_path, "2026-11-04T09:00:00+00:00", "2026-11-04T12:00:00+00:00")
    assert rep["learning"]["tried_session"] == 120
