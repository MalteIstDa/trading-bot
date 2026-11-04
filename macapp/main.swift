// Trading Bot fuer den Mac: ein eigenes Fenster mit dem Dashboard.
// Startet beim Oeffnen den Dashboard-Server aus ~/trading-bot (falls er nicht schon laeuft)
// und beendet ihn beim Schliessen wieder. Der Paper-Bot selbst wird ueber die Knoepfe im Dashboard
// gestartet und pausiert und laeuft unabhaengig von diesem Fenster weiter.
import Cocoa
import WebKit

let port = 8052
let dashboardURL = URL(string: "http://127.0.0.1:\(port)/?bot=copy")!

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var tries = 0

    var repo: String {
        (Bundle.main.object(forInfoDictionaryKey: "RepoPath") as? String) ?? (NSHomeDirectory() + "/trading-bot")
    }

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        let config = WKWebViewConfiguration()
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.uiDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Trading Bot"
        window.contentView = web
        window.center()
        window.setFrameAutosaveName("TradingBotWindow")
        window.makeKeyAndOrderFront(nil)
        showMessage("Dashboard wird gestartet …")
        if !serverIsUp() { startServer() }
        waitAndLoad()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ app: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ note: Notification) {
        server?.terminate()  // nur das Dashboard; ein laufender Bot macht weiter
    }

    // MARK: Server

    func serverIsUp() -> Bool {
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/api/control")!)
        req.timeoutInterval = 1
        let sem = DispatchSemaphore(value: 0)
        var ok = false
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            ok = resp != nil
            sem.signal()
        }.resume()
        _ = sem.wait(timeout: .now() + 1.5)
        return ok
    }

    func startServer() {
        let exe = repo + "/.venv/bin/tradingbot"
        guard FileManager.default.isExecutableFile(atPath: exe) else {
            showMessage("Nicht gefunden: \(exe)<br>Ist der Ordner trading-bot mit .venv eingerichtet? (siehe README)")
            return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = ["dashboard", "--port", String(port), "--no-browser"]
        p.currentDirectoryURL = URL(fileURLWithPath: repo)
        var env = ProcessInfo.processInfo.environment
        env["TRADINGBOT_DATA"] = "data/copy"
        p.environment = env
        let logURL = URL(fileURLWithPath: repo + "/data/copy/dashboard.log")
        try? FileManager.default.createDirectory(atPath: repo + "/data/copy", withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        if let log = try? FileHandle(forWritingTo: logURL) {
            log.seekToEndOfFile()
            p.standardOutput = log
            p.standardError = log
        }
        do { try p.run(); server = p } catch {
            showMessage("Dashboard konnte nicht gestartet werden: \(error.localizedDescription)")
        }
    }

    func waitAndLoad() {
        DispatchQueue.global().async {
            let up = self.serverIsUp()
            DispatchQueue.main.async {
                if up {
                    self.web.load(URLRequest(url: dashboardURL))
                } else if self.tries < 40 {
                    self.tries += 1
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.waitAndLoad() }
                } else {
                    self.showMessage("Das Dashboard antwortet nicht. Protokoll: \(self.repo)/data/copy/dashboard.log")
                }
            }
        }
    }

    func showMessage(_ html: String) {
        web.loadHTMLString("""
        <html><body style="background:#070b12;color:#d7e0ea;font:14px ui-monospace,Menlo,monospace;
        display:flex;align-items:center;justify-content:center;height:90vh;text-align:center">
        <div><b>TRADING BOT</b><br><br>\(html)</div></body></html>
        """, baseURL: nil)
    }

    // Links nach aussen (z. B. TradingView, Hyperliquid) im normalen Browser oeffnen
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let url = action.request.url, let host = url.host, host != "127.0.0.1", host != "localhost",
           action.navigationType == .linkActivated {
            NSWorkspace.shared.open(url)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url { NSWorkspace.shared.open(url) }
        return nil
    }

    // CSV-Export: Download in den Downloads-Ordner
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        if let url = response.response.url, url.path == "/api/trades.csv" {
            decisionHandler(.cancel)
            URLSession.shared.downloadTask(with: url) { tmp, _, _ in
                guard let tmp = tmp else { return }
                let dest = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
                    .appendingPathComponent("paper_trades.csv")
                try? FileManager.default.removeItem(at: dest)
                try? FileManager.default.moveItem(at: tmp, to: dest)
                DispatchQueue.main.async { NSWorkspace.shared.activateFileViewerSelecting([dest]) }
            }.resume()
            return
        }
        decisionHandler(.allow)
    }

    // MARK: Menue

    @objc func reload() { web.load(URLRequest(url: dashboardURL)) }
    @objc func openInBrowser() { NSWorkspace.shared.open(dashboardURL) }
    @objc func openFolder() { NSWorkspace.shared.open(URL(fileURLWithPath: repo)) }

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Trading Bot beenden", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu

        let editItem = NSMenuItem(); main.addItem(editItem)
        let edit = NSMenu(title: "Bearbeiten")
        edit.addItem(withTitle: "Ausschneiden", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Kopieren", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Einsetzen", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Alles auswählen", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit

        let viewItem = NSMenuItem(); main.addItem(viewItem)
        let view = NSMenu(title: "Ansicht")
        view.addItem(withTitle: "Neu laden", action: #selector(reload), keyEquivalent: "r")
        view.addItem(withTitle: "Im Browser öffnen", action: #selector(openInBrowser), keyEquivalent: "b")
        view.addItem(withTitle: "Ordner trading-bot zeigen", action: #selector(openFolder), keyEquivalent: "")
        viewItem.submenu = view
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.activate(ignoringOtherApps: true)
app.run()
