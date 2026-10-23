// Standalone, dependency-free unit tests. Build with -D TURN_STOPPER_TESTS.
// Every TurnStopper is injected with FakeAX; this executable never inspects or
// presses a real UI, asks for permissions, launches an engine or sends a signal.
#if TURN_STOPPER_TESTS
import Foundation

private final class FakeAX: TurnStopAXBackend {
    typealias Element = Int
    var trusted = true
    var apps = [TurnStopApplication(pid: 42, bundleIdentifier: "com.openai.codex",
                                    launchDate: Date(timeIntervalSince1970: 100))]
    var screens = [CGRect(x: 0, y: 0, width: 1_920, height: 1_080)]
    var appWindows = [42: [1]]
    var nodes = [1: TurnStopNode(role: "AXWindow"), 2: TurnStopNode(role: "AXGroup"),
                 3: TurnStopNode(role: "AXButton"), 9: TurnStopNode(role: "AXTextArea")]
    var windowStates = [1: TurnStopWindow(frame: CGRect(x: 0, y: 0, width: 1_000, height: 800))]
    var childMap = [1: [2], 2: [3, 9]]
    var extraFrames = [9: CGRect(x: 100, y: 700, width: 750, height: 60)]
    var editable: Set<Int> = [9]
    var buttons = [3: TurnStopButton(title: "Stop generating", frame: CGRect(x: 900, y: 700, width: 32, height: 32))]
    var pids = [1: pid_t(42), 3: pid_t(42), 9: pid_t(42)]
    var containingWindows = [3: 1, 9: 1]
    var failReads = false
    var pressSucceeds = true
    var pressed: [Int] = []
    var applicationReads = 0
    var buttonReads = 0
    var nodeReads = 0
    var onApplicationRead: ((FakeAX) -> Void)?
    var onButtonRead: ((FakeAX, Int) -> Void)?

    func isTrusted() -> Bool { trusted }
    func applications() -> [TurnStopApplication] {
        applicationReads += 1
        onApplicationRead?(self)
        return apps
    }
    func screenFrames() throws -> [CGRect] { try check(); return screens }
    func windows(of application: TurnStopApplication) throws -> [Int] {
        try check(); return appWindows[Int(application.pid)] ?? []
    }
    func node(_ element: Int) throws -> TurnStopNode {
        nodeReads += 1
        try check()
        guard let value = nodes[element] else { throw TurnStopAXError.unavailable }
        return value
    }
    func window(_ element: Int) throws -> TurnStopWindow {
        try check()
        guard let value = windowStates[element] else { throw TurnStopAXError.unavailable }
        return value
    }
    func frame(_ element: Int) throws -> CGRect {
        try check()
        guard let value = extraFrames[element] ?? buttons[element]?.frame ?? windowStates[element]?.frame else {
            throw TurnStopAXError.unavailable
        }
        return value
    }
    func isEditableTextArea(_ element: Int) throws -> Bool { try check(); return editable.contains(element) }
    func children(_ element: Int) throws -> [Int] { try check(); return childMap[element] ?? [] }
    func button(_ element: Int) throws -> TurnStopButton {
        buttonReads += 1
        onButtonRead?(self, element)
        try check()
        guard let value = buttons[element] else { throw TurnStopAXError.unavailable }
        return value
    }
    func ownerPID(_ element: Int) throws -> pid_t {
        try check()
        guard let value = pids[element] else { throw TurnStopAXError.unavailable }
        return value
    }
    func containingWindow(_ element: Int) throws -> Int {
        try check()
        guard let value = containingWindows[element] else { throw TurnStopAXError.unavailable }
        return value
    }
    func press(_ element: Int) -> Bool { pressed.append(element); return pressSucceeds }
    private func check() throws { if failReads { throw TurnStopAXError.unavailable } }

    func addButton(_ id: Int, title: String = "Stop generating") {
        nodes[id] = TurnStopNode(role: "AXButton")
        buttons[id] = TurnStopButton(title: title, frame: CGRect(x: 850, y: 700, width: 32, height: 32))
        pids[id] = 42
        containingWindows[id] = 1
        childMap[2, default: []].append(id)
    }
}

private struct TestFailure: Error, CustomStringConvertible {
    let description: String
}

private func expect<T: Equatable>(_ actual: T, _ expected: T,
                                   file: StaticString = #filePath, line: UInt = #line) throws {
    guard actual == expected else {
        throw TestFailure(description: "\(file):\(line): expected \(expected), received \(actual)")
    }
}

private func noAction(_ setup: (FakeAX) -> Void, capability: TurnStopper.Capability,
                      outcome: TurnStopper.Outcome) throws {
    let fake = FakeAX()
    setup(fake)
    let stopper = TurnStopper(backend: fake)
    try expect(stopper.capability(), capability)
    try expect(stopper.interrupt(), outcome)
    try expect(fake.pressed, [])
}

@main
private struct TurnStopperTests {
    static func main() throws {
        let tests: [(String, () throws -> Void)] = [
            ("construction and capability never press", {
                let fake = FakeAX()
                let stopper = TurnStopper(backend: fake)
                try expect(fake.applicationReads, 0)
                try expect(stopper.capability(), .ready)
                try expect(stopper.capability().canInterrupt, true)
                try expect(fake.pressed, [])
            }),
            ("one revalidated generation button is pressed once", {
                let fake = FakeAX()
                try expect(TurnStopper(backend: fake).interrupt(), .stopRequested)
                try expect(fake.pressed, [3])
                try expect(fake.buttonReads, 3)
            }),
            ("permission denial does not inspect applications", {
                let fake = FakeAX()
                fake.trusted = false
                let stopper = TurnStopper(backend: fake)
                try expect(stopper.capability(), .permissionRequired)
                try expect(stopper.interrupt(), .permissionRequired)
                try expect(fake.applicationReads, 0)
                try expect(fake.pressed, [])
            }),
            ("absent app and unverified bundle ID are ignored", {
                try noAction({ $0.apps = [] }, capability: .appNotRunning, outcome: .appNotRunning)
                try noAction({ $0.apps[0].bundleIdentifier = "com.openai.chat" }, capability: .appNotRunning, outcome: .appNotRunning)
                try noAction({ $0.apps[0].bundleIdentifier = "com.example.editor" }, capability: .appNotRunning, outcome: .appNotRunning)
            }),
            ("hidden and minimized apps have no visible capability", {
                try noAction({ $0.apps[0].hidden = true }, capability: .unavailable(.noVisibleWindow), outcome: .unavailable(.noVisibleWindow))
                try noAction({ $0.windowStates[1]?.minimized = true }, capability: .unavailable(.noVisibleWindow), outcome: .unavailable(.noVisibleWindow))
                try noAction({ $0.nodes[1]?.hidden = true }, capability: .unavailable(.noVisibleWindow), outcome: .unavailable(.noVisibleWindow))
            }),
            ("no windows and offscreen windows are unavailable", {
                try noAction({ $0.appWindows = [:] }, capability: .unavailable(.noVisibleWindow), outcome: .unavailable(.noVisibleWindow))
                try noAction({ $0.windowStates[1]?.frame.origin.x = 5_000 }, capability: .unavailable(.noVisibleWindow), outcome: .unavailable(.noVisibleWindow))
            }),
            ("no active control is an explicit harmless noop", {
                try noAction({ $0.childMap[2] = [] }, capability: .noActiveTurn, outcome: .noActiveTurn)
                try expect(TurnStopper.Capability.noActiveTurn.canInterrupt, false)
                try expect(TurnStopper.Capability.noActiveTurn.message.contains("Background"), true)
            }),
            ("a generic Stop is never guessed", {
                try noAction({ $0.buttons[3]?.title = "Stop" }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
            }),
            ("Stop requires a precise generation description or help", {
                let fake = FakeAX()
                fake.buttons[3]?.title = "Stop"
                fake.buttons[3]?.help = "Stop generating"
                try expect(TurnStopper(backend: fake).interrupt(), .stopRequested)
                try expect(fake.pressed, [3])
            }),
            ("exact labels only; media, substrings and localized guesses ignored", {
                for title in ["Stop recording", "Stop playback", "Stop server", "Stop all", "Stop generating now", "Restart", "Stop generating (Esc)", "Detener generación", "stop generating"] {
                    try noAction({ $0.buttons[3]?.title = title }, capability: .noActiveTurn, outcome: .noActiveTurn)
                }
            }),
            ("conversation titles and labels outside a composer cannot trigger Stop", {
                try noAction({ $0.childMap[2] = [3] }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
                try noAction({ $0.editable = [] }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
                try noAction({ $0.nodes[9]?.role = "AXTextField" }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
            }),
            ("a composer in another scroll area cannot justify a sidebar control", {
                try noAction({ fake in
                    fake.childMap[2] = [3, 8]
                    fake.nodes[8] = TurnStopNode(role: "AXScrollArea")
                    fake.childMap[8] = [9]
                }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
            }),
            ("editor must be visible and owned by the target app window", {
                try noAction({ $0.pids[9] = 77 }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
                try noAction({ $0.containingWindows[9] = 99 }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
                try noAction({ $0.extraFrames[9]?.origin.x = 2_000 }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
            }),
            ("conflicting labels fail closed", {
                try noAction({ $0.buttons[3]?.description = "Stop recording" }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
            }),
            ("disabled, hidden and zero size buttons are not active", {
                try noAction({ $0.buttons[3]?.enabled = false }, capability: .noActiveTurn, outcome: .noActiveTurn)
                try noAction({ $0.nodes[3]?.hidden = true }, capability: .noActiveTurn, outcome: .noActiveTurn)
                try noAction({ $0.buttons[3]?.frame.size = .zero }, capability: .noActiveTurn, outcome: .noActiveTurn)
            }),
            ("a button outside its window cannot be pressed", {
                try noAction({ $0.buttons[3]?.frame.origin = CGPoint(x: 1_500, y: 900) }, capability: .noActiveTurn, outcome: .noActiveTurn)
            }),
            ("missing enabled state or AXPress is unsupported", {
                try noAction({ $0.buttons[3]?.enabled = nil }, capability: .unavailable(.unsupportedControl), outcome: .unavailable(.unsupportedControl))
                try noAction({ $0.buttons[3]?.supportsPress = false }, capability: .unavailable(.unsupportedControl), outcome: .unavailable(.unsupportedControl))
            }),
            ("multiple targets and an unresolved Stop prevent selection", {
                try noAction({ $0.addButton(4) }, capability: .unavailable(.ambiguousControls), outcome: .unavailable(.ambiguousControls))
                try noAction({ $0.addButton(4, title: "Stop") }, capability: .unavailable(.unverifiedStopControl), outcome: .unavailable(.unverifiedStopControl))
            }),
            ("multiple windows are not resolved by choosing the first", {
                try noAction({ fake in
                    fake.appWindows[42] = [1, 10]
                    fake.nodes[10] = TurnStopNode(role: "AXWindow")
                    fake.nodes[11] = TurnStopNode(role: "AXGroup")
                    fake.nodes[12] = TurnStopNode(role: "AXTextArea")
                    fake.nodes[13] = TurnStopNode(role: "AXButton")
                    fake.windowStates[10] = fake.windowStates[1]
                    fake.childMap[10] = [11]
                    fake.childMap[11] = [12, 13]
                    fake.buttons[13] = fake.buttons[3]
                    fake.extraFrames[12] = fake.extraFrames[9]
                    fake.editable.insert(12)
                    for id in [10, 12, 13] { fake.pids[id] = 42 }
                    fake.containingWindows[12] = 10
                    fake.containingWindows[13] = 10
                }, capability: .unavailable(.ambiguousControls), outcome: .unavailable(.ambiguousControls))
            }),
            ("wrong PID or window ownership is rejected", {
                try noAction({ $0.pids[3] = 77 }, capability: .unavailable(.accessibilityUnavailable), outcome: .unavailable(.accessibilityUnavailable))
                try noAction({ $0.containingWindows[3] = 99 }, capability: .unavailable(.accessibilityUnavailable), outcome: .unavailable(.accessibilityUnavailable))
                try noAction({ $0.pids[1] = 77 }, capability: .unavailable(.accessibilityUnavailable), outcome: .unavailable(.accessibilityUnavailable))
            }),
            ("missing launch identity is rejected", {
                try noAction({ $0.apps[0].launchDate = nil }, capability: .unavailable(.accessibilityUnavailable), outcome: .unavailable(.accessibilityUnavailable))
            }),
            ("IPC failure cannot be reported as no active turn", {
                try noAction({ $0.failReads = true }, capability: .unavailable(.accessibilityUnavailable), outcome: .unavailable(.accessibilityUnavailable))
            }),
            ("menus, dialogs and transcript text are not traversed", {
                for role in ["AXMenu", "AXSheet", "AXDialog", "AXStaticText", "AXTextArea", "AXLink"] {
                    try noAction({ $0.nodes[2]?.role = role }, capability: .noActiveTurn, outcome: .noActiveTurn)
                }
                try noAction({ $0.nodes[1]?.subrole = "AXDialog" }, capability: .unavailable(.noVisibleWindow), outcome: .unavailable(.noVisibleWindow))
                try noAction({ $0.buttons[3]?.subrole = "AXCloseButton" }, capability: .noActiveTurn, outcome: .noActiveTurn)
            }),
            ("nested browser or tool web content is excluded", {
                try noAction({ fake in
                    fake.nodes[2]?.role = "AXWebArea"
                    fake.nodes[4] = TurnStopNode(role: "AXWebArea")
                    fake.childMap[2] = [4]
                    fake.childMap[4] = [3]
                }, capability: .noActiveTurn, outcome: .noActiveTurn)
            }),
            ("tree cycles and duplicate references do not duplicate presses", {
                let fake = FakeAX()
                fake.childMap[2] = [2, 3, 3, 9]
                try expect(TurnStopper(backend: fake).interrupt(), .stopRequested)
                try expect(fake.pressed, [3])
            }),
            ("truncated or oversized trees fail closed", {
                try noAction({ $0.childMap[2] = Array(10...2_010) }, capability: .unavailable(.inspectionLimitReached), outcome: .unavailable(.inspectionLimitReached))
                try noAction({ fake in
                    fake.childMap[2] = [10]
                    for id in 10...80 {
                        fake.nodes[id] = TurnStopNode(role: "AXGroup")
                        fake.childMap[id] = [id + 1]
                    }
                    fake.nodes[81] = TurnStopNode(role: "AXGroup")
                }, capability: .unavailable(.inspectionLimitReached), outcome: .unavailable(.inspectionLimitReached))
            }),
            ("inspection deadline is a noop", {
                let fake = FakeAX()
                var tick: TimeInterval = 0
                let stopper = TurnStopper(backend: fake, now: { tick += 1; return tick })
                try expect(stopper.capability(), .unavailable(.inspectionLimitReached))
                try expect(fake.pressed, [])
            }),
            ("button relabeling between scans cancels the action", {
                let fake = FakeAX()
                fake.onApplicationRead = { if $0.applicationReads == 2 { $0.buttons[3]?.title = "Send" } }
                try expect(TurnStopper(backend: fake).interrupt(), .targetChanged)
                try expect(fake.pressed, [])
            }),
            ("a prior ready capability never caches an action target", {
                let fake = FakeAX()
                let stopper = TurnStopper(backend: fake)
                try expect(stopper.capability(), .ready)
                fake.buttons[3]?.title = "Send"
                try expect(stopper.interrupt(), .noActiveTurn)
                try expect(fake.pressed, [])
            }),
            ("restarted app with reused PID cancels the action", {
                let fake = FakeAX()
                fake.onApplicationRead = {
                    if $0.applicationReads == 2 { $0.apps[0].launchDate = Date(timeIntervalSince1970: 101) }
                }
                try expect(TurnStopper(backend: fake).interrupt(), .targetChanged)
                try expect(fake.pressed, [])
            }),
            ("a new second control between scans cancels the action", {
                let fake = FakeAX()
                fake.onApplicationRead = { if $0.applicationReads == 2 { $0.addButton(4) } }
                try expect(TurnStopper(backend: fake).interrupt(), .targetChanged)
                try expect(fake.pressed, [])
            }),
            ("last moment relabeling is revalidated", {
                let fake = FakeAX()
                fake.onButtonRead = { fake, id in if fake.buttonReads == 3 { fake.buttons[id]?.title = "Send" } }
                try expect(TurnStopper(backend: fake).interrupt(), .targetChanged)
                try expect(fake.pressed, [])
            }),
            ("last moment composer change prevents dispatch", {
                let fake = FakeAX()
                fake.onButtonRead = { fake, _ in if fake.buttonReads == 3 { fake.editable = [] } }
                try expect(TurnStopper(backend: fake).interrupt(), .targetChanged)
                try expect(fake.pressed, [])
            }),
            ("permission revoked between scans is reported", {
                let fake = FakeAX()
                fake.onApplicationRead = { if $0.applicationReads == 2 { $0.trusted = false } }
                try expect(TurnStopper(backend: fake).interrupt(), .permissionRequired)
                try expect(fake.pressed, [])
            }),
            ("unacknowledged AXPress is attempted once, never retried", {
                let fake = FakeAX()
                fake.pressSucceeds = false
                try expect(TurnStopper(backend: fake).interrupt(), .failed)
                try expect(fake.pressed, [3])
            })
        ]
        for (name, test) in tests {
            do { try test() }
            catch { throw TestFailure(description: "FAIL: \(name): \(error)") }
        }
        print("TurnStopper: \(tests.count) mock-only tests passed; no system AX calls or real Stop actions.")
    }
}
#endif
