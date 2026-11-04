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
