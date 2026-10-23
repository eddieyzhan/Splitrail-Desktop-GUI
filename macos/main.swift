import AppKit
import Darwin

if CommandLine.arguments.contains("--version") {
    print("Splitrail Desktop \(nativeVersion) · native macOS")
    exit(0)
}
if CommandLine.arguments.contains("--self-check") {
    let recovery = RecoverySimulation.run()
    guard recovery.success else { fputs(recovery.detail + "\n", stderr); exit(1) }
    let now = Date().timeIntervalSince1970
    let reading = QuotaReading(timestamp: now, status: "fresh", windows: [QuotaWindow(id: "week", label: "Weekly", kind: "weekly", percent: 40, reset: now + 86400)], expiries: [])
    var guardState = NativeGuard()
    guardState.currentLimit = 50
    try guardState.arm(reading, now: now)
    assert(guardState.armed && !guardState.blocked)
    guardState.checkAge(now: now + 121)
    assert(guardState.blocked)
    assert(shortModel("gpt-6.1-sol") == "Sol")
    assert(compact(41_200_000) == "41.2M")
    assert(!Bundle.main.paths(forResourcesOfType: "framework", inDirectory: "Frameworks").contains { $0.contains("Qt") })
    print("Splitrail \(nativeVersion): native AppKit, quota state and formatting checks passed")
    exit(0)
}
if CommandLine.arguments.contains("--test-recovery") {
    let report = RecoverySimulation.run()
    print(report.detail)
    print(report.summary)
    exit(report.success ? 0 : 1)
}

let demo = CommandLine.arguments.contains("--demo") || CommandLine.arguments.contains("--smoke-ui")
var lockFD: Int32 = -1
if !demo {
    try? FileManager.default.createDirectory(at: appData, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
    lockFD = open(appData.appendingPathComponent("native.lock").path, O_CREAT | O_RDWR, 0o600)
    if lockFD >= 0 && flock(lockFD, LOCK_EX | LOCK_NB) != 0 {
        if let identifier = Bundle.main.bundleIdentifier {
            NSRunningApplication.runningApplications(withBundleIdentifier: identifier).first { $0.processIdentifier != getpid() }?.activate(options: [.activateAllWindows])
        }
        exit(0)
    }
}
let app = NSApplication.shared
let controller = AppController()
app.delegate = controller
app.run()
if lockFD >= 0 { close(lockFD) }
