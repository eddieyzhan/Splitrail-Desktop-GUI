import AppKit
import ApplicationServices

final class AppController: NSObject, NSApplicationDelegate, NSWindowDelegate, NativeActions {
    var cache = NativeCache()
    var syncInfo: [String: Any] = [:]
    var preferences: [String: Any] = [:]
    var guardState = NativeGuard()
    private var operationMessage = ""
    var message: String {
        get { ([operationMessage] + (cache.notices ?? [])).filter { !$0.isEmpty }.joined(separator: " ") }
        set { operationMessage = newValue }
    }
    var page = 0
    var rangePreset = 2
    var selectedRange: (String, String)?
    var busy: Bool { worker.busy || quotaWorker.busy }
    private let worker = Worker(), quotaWorker = Worker(), events = LogEvents()
    private var statusItem: NSStatusItem!
    private var window: NSPanel?
    private var viewController: DashboardViewController?
    private var usageTimer: Timer?, quotaTimer: Timer?, deadlineTimer: Timer?, syncTimer: Timer?
    private var reconcile: NSBackgroundActivityScheduler?
    private var dirty = false, pendingUsage = false, cutoffHandled = false
    private var pendingQuota = false
    private var pendingOperation: (String, [String: Any])?
    private var lastCollection = Date.distantPast
    private var sleeping = false
    private let demo = CommandLine.arguments.contains("--demo")
    private var pinned = false
    private let stopper = TurnStopper()
    private var priceEditor: PriceEditorController?
    private var guardActivity: NSObjectProtocol?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        statusItem.button?.image = NSImage(systemSymbolName: "chart.bar.xaxis", accessibilityDescription: "Splitrail")
        statusItem.button?.target = self; statusItem.button?.action = #selector(togglePanel(_:))
        statusItem.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])
        let menu = NSMenu()
        for (title, action, key) in [("Show Splitrail", #selector(showPanel), ""), ("Keep on screen", #selector(togglePin(_:)), ""),
                                      ("Refresh", #selector(manualRefresh), "r"), ("Quit Splitrail", #selector(quit), "q")] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key); item.target = self; menu.addItem(item)
        }
        contextMenu = menu
        if demo { loadDemo() } else {
            if let data = try? Data(contentsOf: appData.appendingPathComponent("native-cache-v1.json")),
               data.count < 25 * 1024 * 1024, let saved = try? JSONDecoder().decode(NativeCache.self, from: data) { cache = saved }
            loadLocalPreferences()
            events.changed = { [weak self] in self?.logsChanged() }; events.start()
            let activity = NSBackgroundActivityScheduler(identifier: "io.github.eddieyzhan.splitrail.reconcile")
            activity.interval = 4 * 3600; activity.tolerance = 30 * 60; activity.repeats = true; activity.qualityOfService = .background
            activity.schedule { [weak self] completion in
                DispatchQueue.main.async { self?.dirty = true; self?.collectUsage(); completion(.finished) }
            }
            reconcile = activity
            NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(willSleep), name: NSWorkspace.willSleepNotification, object: nil)
            NSWorkspace.shared.notificationCenter.addObserver(self, selector: #selector(didWake), name: NSWorkspace.didWakeNotification, object: nil)
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
                if self?.worker.busy == false { self?.run("settings") }
                if self?.lastCollection == Date.distantPast { self?.dirty = true; self?.collectUsage() }
            }
        }
        if !CommandLine.arguments.contains("--background") { showPanel() }
        if CommandLine.arguments.contains("--smoke-ui") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
                self?.page = 1; self?.render()
                self?.page = 2; self?.render()
                self?.page = 0; self?.render()
                if let path = ProcessInfo.processInfo.environment["SPLITRAIL_SCREENSHOT"] { self?.captureDemo(to: path) }
                NSApp.terminate(nil)
            }
        }
    }

    private var contextMenu: NSMenu!
    @objc private func togglePanel(_ sender: Any?) {
        if NSApp.currentEvent?.type == .rightMouseUp {
            if let button = statusItem.button { contextMenu.popUp(positioning: nil, at: NSPoint(x: 0, y: button.bounds.maxY + 4), in: button) }
        } else if window?.isVisible == true { window?.close() } else { showPanel() }
    }
    @objc func showPanel() {
        if let window { window.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true); return }
        let content = DashboardViewController(actions: self)
        let panel = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 450, height: 370),
                            styleMask: [.titled, .closable, .miniaturizable], backing: .buffered, defer: false)
        panel.title = "Splitrail"; panel.titleVisibility = .hidden; panel.titlebarAppearsTransparent = true
        panel.isReleasedWhenClosed = false; panel.hidesOnDeactivate = false; panel.delegate = self
        panel.level = pinned ? .floating : .normal
        panel.contentViewController = content; viewController = content; window = panel
        applyTheme(); panel.center(); panel.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true)
        render()
        if !demo {
            if dirty || cache.days.isEmpty || Date().timeIntervalSince1970 - cache.timestamp > 4 * 3600 { collectUsage() }
            if Date().timeIntervalSince1970 - (cache.quota?.timestamp ?? 0) > 60 { refreshQuota() }
            else { scheduleQuota() }
        }
    }
    @objc private func togglePin(_ sender: NSMenuItem) {
        pinned.toggle(); sender.state = pinned ? .on : .off; window?.level = pinned ? .floating : .normal
        if pinned { showPanel() }
    }
    func windowWillClose(_ notification: Notification) {
        if let closed = notification.object as? NSWindow {
            closed.contentViewController = nil
            closed.contentView = nil
        }
        viewController = nil; window = nil
        scheduleQuota()
        if dirty { scheduleUsage() }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { showPanel(); return true }
    @objc private func quit() { NSApp.terminate(nil) }
    func applicationWillTerminate(_ notification: Notification) {
        worker.cancel(); quotaWorker.cancel(); reconcile?.invalidate()
        for timer in [usageTimer, quotaTimer, deadlineTimer, syncTimer] { timer?.invalidate() }
        NSWorkspace.shared.notificationCenter.removeObserver(self)
    }
    @objc private func manualRefresh() { action("refresh", options: [:]) }
    @objc private func willSleep() {
        sleeping = true
        for timer in [usageTimer, quotaTimer, deadlineTimer, syncTimer] { timer?.invalidate() }
    }
    @objc private func didWake() {
        sleeping = false
        if guardState.armed { guardState.checkAge(now: Date().timeIntervalSince1970); enforceGuard() }
        dirty = true; scheduleUsage(); scheduleSync(); refreshQuota()
    }

    func action(_ name: String, options: [String: Any] = [:]) {
        switch name {
        case "refresh": message = ""; dirty = true; collectUsage(); refreshQuota()
        case "sync": run("sync")
        case "arm": arm(options)
        case "disarm":
            guardState.disarm(); cutoffHandled = false; message = ""; deadlineTimer?.invalidate()
            if let guardActivity { ProcessInfo.processInfo.endActivity(guardActivity); self.guardActivity = nil }
            scheduleQuota(); render()
        case "permission": requestStopPermission()
        case "export", "import": transfer(name)
        case "prices": openPrices()
        default: run(name, options: options)
        }
    }

    private func timer(after seconds: TimeInterval, tolerance: TimeInterval, _ block: @escaping () -> Void) -> Timer {
        let timer = Timer.scheduledTimer(withTimeInterval: max(0.1, seconds), repeats: false) { _ in block() }
        timer.tolerance = tolerance; return timer
    }
    private func logsChanged() { dirty = true; scheduleUsage() }
    private func scheduleUsage() {
        guard !demo, !sleeping, dirty, usageTimer == nil || usageTimer?.isValid == false else { return }
        let cadence: TimeInterval = window == nil ? 300 : 60
        usageTimer = timer(after: max(window == nil ? 30 : 5, cadence - Date().timeIntervalSince(lastCollection)), tolerance: 10) { [weak self] in self?.collectUsage() }
    }
    private func collectUsage() {
        guard !demo, !sleeping else { return }
        usageTimer?.invalidate(); usageTimer = nil
        if worker.busy || quotaWorker.busy { pendingUsage = true; return }
        dirty = false; pendingUsage = false; lastCollection = Date()
        run("usage")
    }
    private func scheduleQuota() {
        quotaTimer?.invalidate(); quotaTimer = nil
        guard !demo, !sleeping, !guardState.blocked, guardState.armed || window != nil else { return }
        let period: TimeInterval = guardState.armed ? 45 : 300
        quotaTimer = timer(after: period, tolerance: guardState.armed ? 3 : 30) { [weak self] in self?.refreshQuota() }
    }
    private func refreshQuota() {
        guard !demo, !sleeping, !quotaWorker.busy else { return }
        if worker.busy && !guardState.armed { pendingQuota = true; return }
        pendingQuota = false
        quotaTimer?.invalidate()
        quotaWorker.run("quota") { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let value):
                do {
                    let reading = try jsonDecode(QuotaReading.self, value)
                    guard reading.timestamp.isFinite, reading.windows.allSatisfy({ ($0.percent?.isFinite ?? true) && ($0.reset?.isFinite ?? true) }) else {
                        throw NativeError.message("Invalid quota reading.")
                    }
                    self.cache.quota = reading
                    self.guardState.observe(reading, now: Date().timeIntervalSince1970)
                    self.saveCache(); self.scheduleDeadline(); self.enforceGuard()
                } catch { self.message = "Quota unavailable. Previous totals are retained." }
            case .failure: self.message = "Quota unavailable. Previous totals are retained."
            }
            self.scheduleQuota(); self.render()
            if let pending = self.pendingOperation {
                self.pendingOperation = nil; self.run(pending.0, options: pending.1)
            } else if self.pendingUsage { self.collectUsage() }
        }
        render()
    }
    private func scheduleDeadline() {
        deadlineTimer?.invalidate(); deadlineTimer = nil
        guard guardState.armed, !guardState.blocked, let stamp = guardState.previous?.timestamp else { return }
        deadlineTimer = timer(after: max(0.1, stamp + 120.1 - Date().timeIntervalSince1970), tolerance: 0) { [weak self] in
            guard let self else { return }
            self.guardState.checkAge(now: Date().timeIntervalSince1970); self.enforceGuard(); self.render()
        }
    }
    private func scheduleSync() {
        syncTimer?.invalidate(); syncTimer = nil
        guard !demo, !sleeping, syncInfo["automatic"] as? Bool == true else { return }
        let delay = syncInfo["nextSyncSeconds"] as? Double ?? 3600
        syncTimer = timer(after: max(30, delay), tolerance: min(300, max(0, delay * 0.1))) { [weak self] in
            guard let self else { return }
            if self.worker.busy { self.syncInfo["nextSyncSeconds"] = 60.0; self.scheduleSync() }
            else { self.run("sync", options: ["automatic": true]) }
        }
    }
    private func run(_ task: String, options: [String: Any] = [:]) {
        guard !demo else { message = "Demo"; render(); return }
        if quotaWorker.busy && !guardState.armed {
            if pendingOperation == nil { pendingOperation = (task, options) }
            return
        }
        guard !worker.busy else { message = "An operation is already running."; render(); return }
        worker.run(task, options: options) { [weak self] result in
            guard let self else { return }
            switch result {
            case .success(let value):
                if let rows = value["days"] {
                    do {
                        self.cache.days = try jsonDecode([UsageDay].self, rows)
                        self.cache.timestamp = value["timestamp"] as? Double ?? Date().timeIntervalSince1970
                        self.cache.notices = value["diagnostics"] as? [String]
                        self.saveCache()
                    } catch { self.message = "Usage data could not be read. Previous totals are retained." }
                }
                if let config = value["sync"] as? [String: Any] { self.syncInfo = config }
                else if task == "settings" || task == "configure" || task == "disconnect" { self.syncInfo = value }
                if let prefs = value["preferences"] as? [String: Any] { self.preferences = prefs }
                if task == "preferences" {
                    self.loadLocalPreferences()
                    if options["usage_mode"] != nil { self.pendingUsage = true }
                }
                if task == "sync" { self.message = "" }
                self.applyTheme(); self.scheduleSync()
            case .failure(let error):
                self.message = error.localizedDescription
                if task == "usage" { self.dirty = true; self.scheduleUsage() }
                if task == "sync" { self.syncInfo["nextSyncSeconds"] = 3600.0; self.scheduleSync() }
            }
            self.render()
            if self.pendingQuota { self.refreshQuota() }
            else if self.pendingUsage { self.collectUsage() }
        }
        render()
    }
    private func loadLocalPreferences() {
        if let data = try? Data(contentsOf: appData.appendingPathComponent("preferences.json")),
           let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] { preferences = value }
        if let limits = preferences["quota_limits"] as? [String: Double] {
            guardState.currentLimit = limits["current"] ?? 95; guardState.resetLimit = limits["after_reset"] ?? 5
        }
    }
    private func saveCache() { if !demo, let data = try? JSONEncoder().encode(cache) { try? savePrivate(data, to: appData.appendingPathComponent("native-cache-v1.json")) } }
    private func render() { viewController?.refresh(); statusItem?.button?.toolTip = guardState.armed ? "Splitrail · cutoff \(guardState.blocked ? "triggered" : "armed")" : "Splitrail" }
    private func applyTheme() { window?.appearance = NSAppearance(named: preferences["theme"] as? String == "Nord" ? .darkAqua : .aqua) }

    // Implemented with the verified turn-control adapter, never process termination.
    private func arm(_ options: [String: Any]) {
        guard !demo else { message = "Demo: no cutoff is activated."; render(); return }
        guardState.currentLimit = options["currentLimit"] as? Double ?? 95
        guardState.resetLimit = options["resetLimit"] as? Double ?? 5
        message = "Checking Stop control…"; render()
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            let capability = self.stopper.capability()
            DispatchQueue.main.async {
                guard capability.canInterrupt else { self.message = capability.message; self.render(); return }
                do {
                    try self.guardState.arm(self.cache.quota, now: Date().timeIntervalSince1970)
                    self.cutoffHandled = false; self.message = ""; self.scheduleDeadline(); self.scheduleQuota()
                    if self.guardActivity == nil {
                        self.guardActivity = ProcessInfo.processInfo.beginActivity(options: .userInitiatedAllowingIdleSystemSleep, reason: "User-armed quota cutoff")
                    }
                    self.enforceGuard()
                    self.run("preferences", options: ["quota_limits": ["current": self.guardState.currentLimit, "after_reset": self.guardState.resetLimit]])
                } catch { self.message = error.localizedDescription }
                self.render()
            }
        }
    }
    private func enforceGuard() {
        guard guardState.armed, guardState.blocked, !cutoffHandled else { return }
        cutoffHandled = true; quotaTimer?.invalidate(); deadlineTimer?.invalidate()
        performStop()
    }
    private func requestStopPermission() {
        guard !demo else { return }
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        let trusted = AXIsProcessTrustedWithOptions(options)
        message = trusted ? "Accessibility is enabled." : "Enable Splitrail in System Settings → Privacy & Security → Accessibility."
        if !trusted { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!) }
        render()
    }
    private func performStop() {
        DispatchQueue.global(qos: .utility).async { [weak self] in
            guard let self else { return }
            let result = self.stopper.interrupt()
            DispatchQueue.main.async {
                self.message = self.guardState.reason + " " + result.message
                if let activity = self.guardActivity { ProcessInfo.processInfo.endActivity(activity); self.guardActivity = nil }
                self.page = 1; self.showPanel(); self.render(); NSSound.beep()
            }
        }
    }

    private func transfer(_ name: String) {
        if name == "export" {
            let dialog = NSSavePanel(); dialog.nameFieldStringValue = "splitrail-usage.json.gz"
            if dialog.runModal() == .OK, let url = dialog.url { run("export", options: ["path": url.path]) }
        } else {
            let dialog = NSOpenPanel(); dialog.allowsMultipleSelection = false; dialog.canChooseDirectories = false
            if dialog.runModal() == .OK, let url = dialog.url { run("import", options: ["path": url.path]) }
        }
    }
    private func openPrices() {
        if demo { message = "Demo"; render(); return }
        let editor = PriceEditorController(); priceEditor = editor; editor.show()
    }

    private func loadDemo() {
        let now = Date().timeIntervalSince1970
        cache = NativeCache(days: [UsageDay(day: Dates.day.string(from: Date()), cost: 482.76, tokens: 41_210_000, conversations: 38,
                    models: [UsageModel(name: "gpt-6-astra", cost: 382.19, tokens: 31_000_000), UsageModel(name: "gpt-6.1-sol", cost: 100.57, tokens: 10_210_000)])],
                    timestamp: now - 120,
                    quota: QuotaReading(timestamp: now, status: "fresh", windows: [QuotaWindow(id: "weekly", label: "Weekly", kind: "weekly", percent: 38, reset: now + 3 * 86400)], expiries: [now + 6 * 86400, now + 13 * 86400]))
        syncInfo = ["connected": true, "repository": "example/private-usage", "automatic": true, "receiveOnly": false, "scope": "codex", "intervalHours": 1, "idleHours": 3, "devices": 3]
        preferences = ["theme": CommandLine.arguments.contains("--dark") ? "Nord" : "Pearl", "automatic_prices": true]
    }
    private func captureDemo(to path: String) {
        guard demo, let view = window?.contentView, let bitmap = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: bitmap)
        if let png = bitmap.representation(using: .png, properties: [:]) { try? png.write(to: URL(fileURLWithPath: path)) }
    }
}
