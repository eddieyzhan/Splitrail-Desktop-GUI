import AppKit
import ApplicationServices
import Foundation

/// Requests the exposed generation Stop action without closing the app or its engine.
/// Calls are synchronous and serialized. Use a background queue, not the AppKit UI thread.
/// Neither method prompts for Accessibility permission. No live action occurs at init.
public final class TurnStopper: @unchecked Sendable {
    public enum Reason: Equatable, Sendable {
        case noVisibleWindow
        case ambiguousControls
        case unverifiedStopControl
        case unsupportedControl
        case accessibilityUnavailable
        case inspectionLimitReached

        public var message: String {
            switch self {
            case .noVisibleWindow:
                return "No accessible, non-minimized ChatGPT window is on screen. Background turns may still be running."
            case .ambiguousControls:
                return "Multiple generation Stop controls are exposed; no turn was selected."
            case .unverifiedStopControl:
                return "A Stop control is exposed, but its generation purpose could not be verified."
            case .unsupportedControl:
                return "The generation Stop control does not expose a supported Accessibility action."
            case .accessibilityUnavailable:
                return "The ChatGPT controls could not be safely inspected."
            case .inspectionLimitReached:
                return "Control inspection reached its time or size limit; no Stop action was sent."
            }
        }
    }

    public enum Capability: Equatable, Sendable {
        case ready
        case permissionRequired
        case appNotRunning
        /// No supported Stop control was found; this does not rule out background turns.
        case noActiveTurn
        case unavailable(Reason)

        public var canInterrupt: Bool { self == .ready }

        public var message: String {
            switch self {
            case .ready: return "One exposed generation Stop control is available."
            case .permissionRequired: return "Accessibility permission is required for the app running TurnStopper."
            case .appNotRunning: return "The supported ChatGPT app is not running."
            case .noActiveTurn: return "No supported generation Stop control is exposed. Background turns may still be running."
            case .unavailable(let reason): return reason.message
            }
        }
    }

    public enum Outcome: Equatable, Sendable {
        /// AXPress was accepted. This is not a server acknowledgement of cancellation.
        case stopRequested
        case permissionRequired
        case appNotRunning
        case noActiveTurn
        case unavailable(Reason)
        case targetChanged
        /// Delivery may be uncertain. Do not automatically retry a failed action.
        case failed

        public var message: String {
            switch self {
            case .stopRequested: return "Generation Stop was requested. The chat and app remain open; cancellation is not confirmed."
            case .permissionRequired: return Capability.permissionRequired.message
            case .appNotRunning: return Capability.appNotRunning.message
            case .noActiveTurn: return Capability.noActiveTurn.message
            case .unavailable(let reason): return reason.message
            case .targetChanged: return "The Stop control changed during inspection; no action was sent."
            case .failed: return "The Stop action was not acknowledged. Delivery is uncertain; do not retry automatically."
            }
        }
    }

    /// Verified by read-only CUA application inventory. Other bundle IDs fail closed.
    public static let supportedBundleIdentifiers: Set<String> = ["com.openai.codex"]

    private let lock = NSLock()
    private let inspect: () -> Capability
    private let requestStop: () -> Outcome

    public convenience init() {
        self.init(backend: SystemTurnStopAX())
    }

    // Injection stays internal: tests never construct or call the real AX backend.
    init<B: TurnStopAXBackend>(backend: B, now: @escaping () -> TimeInterval = {
        ProcessInfo.processInfo.systemUptime
    }) {
        let engine = TurnStopEngine(backend: backend, now: now)
        inspect = { engine.capability() }
        requestStop = { engine.interrupt() }
    }

    public func capability() -> Capability {
        lock.lock()
        defer { lock.unlock() }
        return inspect()
    }

    public func interrupt() -> Outcome {
        lock.lock()
        defer { lock.unlock() }
        return requestStop()
    }
}

// The policy and traversal are platform-independent behind this small AX adapter.
// Only button labels are read, never AXValue, editor text, transcripts or window titles.
struct TurnStopApplication: Equatable {
    var pid: pid_t
    var bundleIdentifier: String
    var launchDate: Date?
    var hidden: Bool = false
}

struct TurnStopNode {
    var role: String
    var hidden: Bool = false
    var subrole: String? = nil
}

struct TurnStopWindow {
    var frame: CGRect
    var minimized: Bool = false
}

struct TurnStopButton: Equatable {
    var title: String? = nil
    var description: String? = nil
    var help: String? = nil
    var subrole: String? = nil
    var enabled: Bool? = true
    var supportsPress: Bool = true
    var frame: CGRect

    enum Recognition: Equatable { case generation, unverifiedStop, other }

    var recognition: Recognition {
        // Exact, case-sensitive labels after outer whitespace trimming. No substring,
        // key equivalent, coordinates, guessed identifiers or global Escape fallback.
        let labels = [title, description, help].compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
        let generationLabels: Set<String> = ["Stop generating", "Stop generation"]
        let neutralLabels: Set<String> = ["", "Stop", "button"]
        guard subrole == nil || subrole == "" || subrole == "AXUnknown" else { return .other }
        if labels.contains(where: generationLabels.contains) {
            // Conflicting descriptions such as "Stop recording" must not be ignored.
            return labels.allSatisfy { generationLabels.contains($0) || neutralLabels.contains($0) }
                ? .generation : .unverifiedStop
        }
        return labels.contains("Stop") ? .unverifiedStop : .other
    }
}

protocol TurnStopAXBackend {
    associatedtype Element: Hashable
    func isTrusted() -> Bool
    func applications() -> [TurnStopApplication]
    func screenFrames() throws -> [CGRect]
    func windows(of application: TurnStopApplication) throws -> [Element]
    func node(_ element: Element) throws -> TurnStopNode
    func window(_ element: Element) throws -> TurnStopWindow
    func frame(_ element: Element) throws -> CGRect
    func isEditableTextArea(_ element: Element) throws -> Bool
    func children(_ element: Element) throws -> [Element]
    func button(_ element: Element) throws -> TurnStopButton
    func ownerPID(_ element: Element) throws -> pid_t
    func containingWindow(_ element: Element) throws -> Element
    func press(_ element: Element) -> Bool
}

private struct TurnStopTarget<Element: Hashable>: Equatable {
    let application: TurnStopApplication
    let window: Element
    let windowFrame: CGRect
    let composer: Element
    let element: Element
    let evidence: TurnStopButton
}

private enum TurnStopInspection<Element: Hashable> {
    case target(TurnStopTarget<Element>)
    case notReady(TurnStopper.Capability)

    var capability: TurnStopper.Capability {
        switch self {
        case .target: return .ready
        case .notReady(let capability): return capability
        }
    }
}

private struct TurnStopEngine<B: TurnStopAXBackend> {
    let backend: B
    let now: () -> TimeInterval
    private let maximumNodes = 2_000
    private let maximumDepth = 64
    private let maximumWindows = 16
    private let inspectionSeconds: TimeInterval = 2

    // Skip controls that cannot be the app's generation button. Nested WebAreas are
    // embedded browser/tool content, not the top-level Electron application surface.
    private let excludedRoles: Set<String> = [
        "AXMenuBar", "AXMenu", "AXMenuItem", "AXSheet", "AXDialog", "AXAlert",
        "AXPopover", "AXStaticText", "AXTextArea", "AXTextField", "AXImage", "AXLink"
    ]

    func capability() -> TurnStopper.Capability { inspect().capability }

    func interrupt() -> TurnStopper.Outcome {
        let deadline = now() + 5
        let first = inspect()
        guard case .target(let target) = first else { return outcome(first.capability) }

        // Never use a cached capability target. A second complete scan also checks
        // uniqueness again; changing windows or disappearing controls fail closed.
        let second = inspect()
        if second.capability == .permissionRequired { return .permissionRequired }
        guard case .target(let current) = second, current == target else { return .targetChanged }
        guard backend.isTrusted() else { return .permissionRequired }
        do {
            let node = try backend.node(target.element)
            let windowNode = try backend.node(target.window)
            let windowState = try backend.window(target.window)
            guard backend.applications().contains(target.application),
                  node.role == "AXButton", !node.hidden,
                  windowNode.role == "AXWindow", !windowNode.hidden,
                  !isDialog(windowNode), !windowState.minimized,
                  windowState.frame == target.windowFrame,
                  isOnScreen(target.evidence.frame, screens: try backend.screenFrames()),
                  try backend.ownerPID(target.element) == target.application.pid,
                  try backend.containingWindow(target.element) == target.window,
                  try backend.button(target.element) == target.evidence,
                  try isComposer(target.composer, containing: target.element, application: target.application,
                                 window: target.window, windowFrame: target.windowFrame,
                                 screens: backend.screenFrames(), deadline: deadline) else { return .targetChanged }
        } catch TurnStopAXError.limit { return .unavailable(.inspectionLimitReached) }
        catch { return backend.isTrusted() ? .targetChanged : .permissionRequired }
        guard backend.isTrusted() else { return .permissionRequired }
        guard now() < deadline else { return .unavailable(.inspectionLimitReached) }
        // The only mutation in this library. No retry, process control or keyboard input.
        return backend.press(target.element) ? .stopRequested : .failed
    }

    private func outcome(_ capability: TurnStopper.Capability) -> TurnStopper.Outcome {
        switch capability {
        case .permissionRequired: return .permissionRequired
        case .appNotRunning: return .appNotRunning
        case .noActiveTurn: return .noActiveTurn
        case .unavailable(let reason): return .unavailable(reason)
        case .ready: return .targetChanged // A ready result always carries a target.
        }
    }

    private func inspect() -> TurnStopInspection<B.Element> {
        guard backend.isTrusted() else { return .notReady(.permissionRequired) }
        let applications = backend.applications().filter {
            TurnStopper.supportedBundleIdentifiers.contains($0.bundleIdentifier)
        }
        guard !applications.isEmpty else { return .notReady(.appNotRunning) }
        let deadline = now() + inspectionSeconds
        var nodes = 0
        var windowCount = 0
        var sawVisibleWindow = false
        var uncertainControl: TurnStopper.Reason?
        var candidates: [TurnStopTarget<B.Element>] = []
        do {
            let screens = try backend.screenFrames()
            guard !screens.isEmpty else { return .notReady(.unavailable(.noVisibleWindow)) }
            for application in applications where !application.hidden {
                // A PID alone is not an application identity (PIDs can be reused).
                guard application.pid > 0, application.launchDate != nil else {
                    return .notReady(.unavailable(.accessibilityUnavailable))
                }
                for window in try backend.windows(of: application) {
                    windowCount += 1
                    guard windowCount <= maximumWindows, now() < deadline else {
                        return .notReady(.unavailable(.inspectionLimitReached))
                    }
                    let windowNode = try backend.node(window)
                    guard windowNode.role == "AXWindow", !windowNode.hidden, !isDialog(windowNode) else { continue }
                    let windowState = try backend.window(window)
                    guard !windowState.minimized, isOnScreen(windowState.frame, screens: screens) else { continue }
                    guard try backend.ownerPID(window) == application.pid else {
                        return .notReady(.unavailable(.accessibilityUnavailable))
                    }
                    sawVisibleWindow = true
                    var visited: Set<B.Element> = []
                    var pending: [(element: B.Element, depth: Int, webDepth: Int, groups: [B.Element])] = [(window, 0, 0, [])]
                    while let entry = pending.popLast() {
                        guard visited.insert(entry.element).inserted else { continue }
                        nodes += 1
                        guard nodes <= maximumNodes, entry.depth <= maximumDepth, now() < deadline else {
                            return .notReady(.unavailable(.inspectionLimitReached))
                        }
                        let node = try backend.node(entry.element)
                        guard !node.hidden, !isDialog(node), !excludedRoles.contains(node.role) else { continue }
                        let webDepth = entry.webDepth + (node.role == "AXWebArea" ? 1 : 0)
                        guard webDepth <= 1 else { continue }
                        if node.role == "AXButton" {
                            let button = try backend.button(entry.element)
                            guard button.recognition != .other,
                                  isOnScreen(button.frame, screens: screens),
                                  windowState.frame.contains(button.frame) else { continue }
                            guard button.enabled != false else { continue }
                            var composer: B.Element?
                            for group in entry.groups.suffix(3).reversed() {
                                if try isComposer(group, containing: entry.element, application: application,
                                                  window: window, windowFrame: windowState.frame,
                                                  screens: screens, deadline: deadline) {
                                    composer = group
                                    break
                                }
                            }
                            guard let composer else {
                                uncertainControl = .unverifiedStopControl
                                continue
                            }
                            if button.recognition == .unverifiedStop {
                                uncertainControl = .unverifiedStopControl
                                continue
                            }
                            guard button.enabled == true, button.supportsPress else {
                                uncertainControl = .unsupportedControl
                                continue
                            }
                            guard try backend.ownerPID(entry.element) == application.pid,
                                  try backend.containingWindow(entry.element) == window else {
                                return .notReady(.unavailable(.accessibilityUnavailable))
                            }
                            candidates.append(TurnStopTarget(application: application, window: window,
                                                             windowFrame: windowState.frame, composer: composer,
                                                             element: entry.element,
                                                             evidence: button))
                            if candidates.count > 1 { return .notReady(.unavailable(.ambiguousControls)) }
                        } else {
                            let children = try backend.children(entry.element)
                            guard children.count <= maximumNodes, pending.count + children.count <= maximumNodes else {
                                return .notReady(.unavailable(.inspectionLimitReached))
                            }
                            let groups = node.role == "AXGroup" ? entry.groups + [entry.element] : entry.groups
                            pending.append(contentsOf: children.map { ($0, entry.depth + 1, webDepth, groups) })
                        }
                    }
                }
            }
            guard now() < deadline else { return .notReady(.unavailable(.inspectionLimitReached)) }
            guard backend.isTrusted() else { return .notReady(.permissionRequired) }
            if let reason = uncertainControl { return .notReady(.unavailable(reason)) }
            if let candidate = candidates.first { return .target(candidate) }
            return .notReady(sawVisibleWindow ? .noActiveTurn : .unavailable(.noVisibleWindow))
        } catch TurnStopAXError.limit {
            return .notReady(.unavailable(.inspectionLimitReached))
        } catch {
            return .notReady(backend.isTrusted() ? .unavailable(.accessibilityUnavailable) : .permissionRequired)
        }
    }

    private func isOnScreen(_ frame: CGRect, screens: [CGRect]) -> Bool {
        guard frame.origin.x.isFinite, frame.origin.y.isFinite,
              frame.width.isFinite, frame.height.isFinite, frame.width > 0, frame.height > 0 else { return false }
        return screens.contains { $0.intersects(frame) }
    }

    private func isDialog(_ node: TurnStopNode) -> Bool {
        node.subrole == "AXDialog" || node.subrole == "AXSystemDialog"
    }

    /// A label in a sidebar, transcript, dialog or embedded page is not sufficient.
    /// Require a compact composer group containing this button and exactly one
    /// editable, visible multiline input. No editor text or placeholder is read.
    private func isComposer(_ group: B.Element, containing button: B.Element,
                            application: TurnStopApplication, window: B.Element, windowFrame: CGRect,
                            screens: [CGRect], deadline: TimeInterval) throws -> Bool {
        var pending: [(B.Element, Int)] = [(group, 0)]
        var visited: Set<B.Element> = []
        var editorCount = 0
        var containsButton = false
        let leaves: Set<String> = ["AXButton", "AXPopUpButton", "AXCheckBox", "AXStaticText", "AXImage"]
        while let (element, depth) = pending.popLast() {
            guard now() < deadline else { throw TurnStopAXError.limit }
            guard visited.insert(element).inserted else { continue }
            guard visited.count <= 32, depth <= 4 else { return false }
            let node = try backend.node(element)
            guard !node.hidden, !isDialog(node) else { return false }
            if node.role == "AXGroup" {
                let children = try backend.children(element)
                guard children.count <= 32, children.count + pending.count <= 32 else { return false }
                pending.append(contentsOf: children.map { ($0, depth + 1) })
            } else if node.role == "AXTextArea" {
                let editorFrame = try backend.frame(element)
                guard try backend.isEditableTextArea(element),
                      try backend.ownerPID(element) == application.pid,
                      try backend.containingWindow(element) == window,
                      windowFrame.contains(editorFrame), isOnScreen(editorFrame, screens: screens) else { return false }
                editorCount += 1
            } else if leaves.contains(node.role) {
                if element == button && node.role == "AXButton" { containsButton = true }
            } else {
                // In particular, never cross a list, scroll area or WebArea to bind
                // a sidebar/transcript button to an unrelated message composer.
                return false
            }
        }
        return containsButton && editorCount == 1
    }
}

enum TurnStopAXError: Error { case unavailable, limit }

private struct SystemTurnStopAX: TurnStopAXBackend {
    struct Element: Hashable {
        let raw: AXUIElement
        static func == (lhs: Element, rhs: Element) -> Bool { CFEqual(lhs.raw, rhs.raw) }
        func hash(into hasher: inout Hasher) { hasher.combine(CFHash(raw)) }
    }

    func isTrusted() -> Bool { AXIsProcessTrusted() }

    func applications() -> [TurnStopApplication] {
        TurnStopper.supportedBundleIdentifiers.sorted().flatMap { bundleID in
            NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).compactMap { application in
                guard !application.isTerminated, application.bundleIdentifier == bundleID else { return nil }
                return TurnStopApplication(pid: application.processIdentifier, bundleIdentifier: bundleID,
                                           launchDate: application.launchDate, hidden: application.isHidden)
            }
        }
    }

    func screenFrames() throws -> [CGRect] {
        var count: UInt32 = 0
        guard CGGetActiveDisplayList(0, nil, &count) == .success, count <= 32 else { throw TurnStopAXError.unavailable }
        var displays = [CGDirectDisplayID](repeating: 0, count: Int(count))
        guard CGGetActiveDisplayList(count, &displays, &count) == .success else { throw TurnStopAXError.unavailable }
        return displays.prefix(Int(count)).map(CGDisplayBounds)
    }

    func windows(of application: TurnStopApplication) throws -> [Element] {
        let app = Element(raw: AXUIElementCreateApplication(application.pid))
        // Check AXHidden as well as Launch Services; never activate or unhide an app.
        if try bool(app, kAXHiddenAttribute as CFString) == true { return [] }
        return try elements(app, kAXWindowsAttribute as CFString, maximum: 16, optional: false) ?? []
    }

    func node(_ element: Element) throws -> TurnStopNode {
        guard let role = try string(element, kAXRoleAttribute as CFString) else { throw TurnStopAXError.unavailable }
        return TurnStopNode(role: role, hidden: try bool(element, kAXHiddenAttribute as CFString) == true,
                            subrole: try string(element, kAXSubroleAttribute as CFString))
    }

    func window(_ element: Element) throws -> TurnStopWindow {
        guard let minimized = try bool(element, kAXMinimizedAttribute as CFString) else { throw TurnStopAXError.unavailable }
        return TurnStopWindow(frame: try frame(element), minimized: minimized)
    }

    func isEditableTextArea(_ element: Element) throws -> Bool {
        guard try string(element, kAXRoleAttribute as CFString) == "AXTextArea",
              try bool(element, kAXEnabledAttribute as CFString) == true else { return false }
        try prepare(element)
        var settable = DarwinBoolean(false)
        // Query only whether AXValue is writable; never fetch its content or set it.
        switch AXUIElementIsAttributeSettable(element.raw, kAXValueAttribute as CFString, &settable) {
        case .success: return settable.boolValue
        case .attributeUnsupported, .noValue, .notImplemented: return false
        default: throw TurnStopAXError.unavailable
        }
    }

    func children(_ element: Element) throws -> [Element] {
        // Prefer the accessibility provider's visible subset. Unsupported/no-value is
        // distinct from an IPC error, which must invalidate the entire inspection.
        if let visible = try elements(element, kAXVisibleChildrenAttribute as CFString, maximum: 2_000) { return visible }
        return try elements(element, kAXChildrenAttribute as CFString, maximum: 2_000) ?? []
    }

    func button(_ element: Element) throws -> TurnStopButton {
        var button = TurnStopButton(
            title: try label(element, kAXTitleAttribute as CFString),
            description: try label(element, kAXDescriptionAttribute as CFString),
            help: try label(element, kAXHelpAttribute as CFString),
            subrole: try string(element, kAXSubroleAttribute as CFString),
            enabled: nil, supportsPress: false, frame: .zero
        )
        guard button.recognition != .other else { return button }
        button.frame = try frame(element)
        button.enabled = try bool(element, kAXEnabledAttribute as CFString)
        guard button.recognition == .generation, button.enabled == true else { return button }
        var actions: CFArray?
        try prepare(element)
        let result = AXUIElementCopyActionNames(element.raw, &actions)
        guard result == .success || result == .actionUnsupported || result == .notImplemented else {
            throw TurnStopAXError.unavailable
        }
        button.supportsPress = (actions as? [String])?.contains(kAXPressAction as String) == true
        return button
    }

    func ownerPID(_ element: Element) throws -> pid_t {
        var pid: pid_t = 0
        guard AXUIElementGetPid(element.raw, &pid) == .success else { throw TurnStopAXError.unavailable }
        return pid
    }

    func containingWindow(_ element: Element) throws -> Element {
        guard let value = try copy(element, kAXWindowAttribute as CFString),
              CFGetTypeID(value) == AXUIElementGetTypeID() else { throw TurnStopAXError.unavailable }
        return Element(raw: unsafeDowncast(value, to: AXUIElement.self))
    }

    func press(_ element: Element) -> Bool {
        do { try prepare(element) } catch { return false }
        return AXUIElementPerformAction(element.raw, kAXPressAction as CFString) == .success
    }

    private func prepare(_ element: Element) throws {
        // Element-local timeout, not a system-wide AX setting. Bounds stalled IPC calls.
        guard AXUIElementSetMessagingTimeout(element.raw, 0.15) == .success else { throw TurnStopAXError.unavailable }
    }

    private func copy(_ element: Element, _ attribute: CFString) throws -> CFTypeRef? {
        try prepare(element)
        var value: CFTypeRef?
        switch AXUIElementCopyAttributeValue(element.raw, attribute, &value) {
        case .success: return value
        case .attributeUnsupported, .noValue: return nil
        default: throw TurnStopAXError.unavailable
        }
    }

    private func string(_ element: Element, _ attribute: CFString) throws -> String? {
        guard let value = try copy(element, attribute) else { return nil }
        guard let string = value as? String else { throw TurnStopAXError.unavailable }
        // Arbitrary button text is transient and is never returned, logged or persisted.
        guard string.utf8.count <= 512 else { return "<unrecognized>" }
        return string
    }

    private func label(_ element: Element, _ attribute: CFString) throws -> String? {
        guard let value = try string(element, attribute) else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        let retained: Set<String> = ["", "Stop", "button", "Stop generating", "Stop generation"]
        // Retain only fixed control labels; discard arbitrary titles/descriptions.
        return retained.contains(trimmed) ? trimmed : "<unrecognized>"
    }

    private func bool(_ element: Element, _ attribute: CFString) throws -> Bool? {
        guard let value = try copy(element, attribute) else { return nil }
        guard CFGetTypeID(value) == CFBooleanGetTypeID(), let boolean = value as? Bool else {
            throw TurnStopAXError.unavailable
        }
        return boolean
    }

    private func elements(_ element: Element, _ attribute: CFString, maximum: Int,
                          optional: Bool = true) throws -> [Element]? {
        try prepare(element)
        var count = 0
        let countResult = AXUIElementGetAttributeValueCount(element.raw, attribute, &count)
        if optional && (countResult == .attributeUnsupported || countResult == .noValue) { return nil }
        guard countResult == .success else { throw TurnStopAXError.unavailable }
        guard count >= 0, count <= maximum else { throw TurnStopAXError.limit }
        if count == 0 { return [] }
        var values: CFArray?
        guard AXUIElementCopyAttributeValues(element.raw, attribute, 0, count, &values) == .success,
              let array = values as? [AnyObject], array.count == count,
              array.allSatisfy({ CFGetTypeID($0) == AXUIElementGetTypeID() }) else { throw TurnStopAXError.unavailable }
        return array.map { Element(raw: unsafeDowncast($0, to: AXUIElement.self)) }
    }

    func frame(_ element: Element) throws -> CGRect {
        guard let position = try copy(element, kAXPositionAttribute as CFString),
              let size = try copy(element, kAXSizeAttribute as CFString),
              CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID() else {
            throw TurnStopAXError.unavailable
        }
        let positionValue = unsafeDowncast(position, to: AXValue.self)
        let sizeValue = unsafeDowncast(size, to: AXValue.self)
        var point = CGPoint.zero
        var dimensions = CGSize.zero
        guard AXValueGetType(positionValue) == .cgPoint, AXValueGetType(sizeValue) == .cgSize,
              AXValueGetValue(positionValue, .cgPoint, &point), AXValueGetValue(sizeValue, .cgSize, &dimensions) else {
            throw TurnStopAXError.unavailable
        }
        return CGRect(origin: point, size: dimensions)
    }
}
