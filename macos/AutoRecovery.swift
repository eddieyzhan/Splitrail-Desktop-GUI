import Foundation

/// Pure policy: no AX, sockets, files, credentials, subprocesses or timers.
/// A future adapter must supply authoritative state and atomically enforce each
/// command's preconditions. The desktop adapter is deliberately NOT implemented.
enum RecoveryMode: Equatable { case normal, fast, unknown }
enum RecoveryPhase: Equatable { case failed, running, completed, interrupted }
enum RecoveryFailureKind: Equatable { case capacity, quota, authentication, network, other }

struct RecoveryTarget: Equatable {
    var applicationInstance: String
    var thread: String
    var turn: String
    var isValid: Bool { !applicationInstance.isEmpty && !thread.isEmpty && !turn.isEmpty }
    func sameConversation(as other: Self) -> Bool {
        applicationInstance == other.applicationInstance && thread == other.thread
    }
}

struct RecoveryFailure: Equatable {
    var id: String
    var kind: RecoveryFailureKind
    /// Continuous-clock deadline, converted by the adapter from Retry-After.
    /// A malformed advertised delay must make the observation unavailable.
    var retryNotBefore: TimeInterval?
}

struct RecoverySnapshot: Equatable {
    var target: RecoveryTarget
    var revision: UInt64
    var observedAt: TimeInterval
    var phase: RecoveryPhase
    var mode: RecoveryMode
    var failure: RecoveryFailure?
    var hasDraft = false
    var needsApproval = false
    var manuallyStopped = false
    var cutoffBlocked = false
    var quotaFresh = true
    var canRetry = true
    var canContinue = true
    var canSetMode = true
    var canStop = true
    /// Must change for user navigation, input, mode changes or a new user turn.
    var userRevision: UInt64 = 0
    func isFresh(at now: TimeInterval) -> Bool {
        observedAt.isFinite && (-0.1...2).contains(now - observedAt)
    }
}

enum RecoveryAction: Equatable {
    case setNormal, setFast, retry, continueOnce, stopForNormal
    var startsTurn: Bool { self == .retry || self == .continueOnce }
}

struct RecoveryCommand: Equatable {
    var id: UInt64
    var action: RecoveryAction
    var expected: RecoverySnapshot
    var expiresAt: TimeInterval
    /// Re-evaluate immediately at dispatch; an acknowledgement alone is not
    /// confirmation. An adapter unable to make this check atomic is unsupported.
    func permits(_ current: RecoverySnapshot, now: TimeInterval) -> Bool {
        guard now.isFinite, now < expiresAt, current.isFresh(at: now),
              current.target == expected.target, current.revision == expected.revision,
              current.userRevision == expected.userRevision,
              current.phase == expected.phase, current.mode == expected.mode,
              current.failure == expected.failure, !current.hasDraft, !current.needsApproval else { return false }
        // Restoring Normal never generates or approves anything, and remains
        // permitted after a cutoff or manual Stop, only on this exact target.
        if action != .setNormal && (current.manuallyStopped || current.cutoffBlocked || !current.quotaFresh) { return false }
        switch action {
        case .setNormal, .setFast: return current.canSetMode
        case .retry: return current.canRetry && current.phase == .failed
        case .continueOnce: return current.canContinue && [.failed, .interrupted].contains(current.phase)
        case .stopForNormal: return current.canStop && current.phase == .running
        }
    }
}

enum RecoveryPause: String, Equatable {
    case unavailable = "Codex integration is not verified."
    case manualStop = "Manual Stop overrides recovery."
    case cutoff = "Usage cutoff overrides recovery."
    case quotaStale = "Fresh quota is required."
    case draft = "Draft left untouched."
    case approval = "Approval left untouched."
    case targetChanged = "Conversation or user input changed."
    case stale = "Live state is unavailable or out of order."
    case unsupported = "Required controls are unavailable."
    case nonCapacity = "This is not a capacity error."
    case retryLimit = "Retry limit reached."
    case fastLimit = "Fast burst limit reached."
    case fastExpired = "Fast recovery window ended."
    case unconfirmed = "Action was not confirmed; no automatic repeat."
    case clockChanged = "Recovery clock is invalid."
}

enum RecoveryState: Equatable {
    case off, watching, waiting, awaitingConfirmation, finished
    case paused(RecoveryPause)
}

/// One explicitly armed conversation/episode, at most four generation attempts,
/// one Continue, one Fast burst, and two Fast bursts/hour with a 15-minute gap.
/// Time is injected, monotonic and must include system sleep. No wall-clock timers.
struct AutoRecovery {
    private(set) var state: RecoveryState = .off
    private(set) var pending: RecoveryCommand?
    private(set) var retryAt: TimeInterval?
    private(set) var fastDeadline: TimeInterval?
    private(set) var needsNormalRestore = false
    private(set) var attempts = 0
    private(set) var continues = 0
    private(set) var fastBursts: [TimeInterval] = []
    private var target: RecoveryTarget?
    private var userRevision: UInt64 = 0
    private var lastRevision: UInt64 = 0
    private var lastSnapshot: RecoverySnapshot?
    private var lastNow: TimeInterval = -.infinity
    private var nextID: UInt64 = 0
    private var seenFailures: Set<String> = []
    private var waitingFailure: String?
    private var usedFast = false
    private var returningToNormal = false
    private var resumeAfterNormal = false
    private var terminalAfterNormal: RecoveryState?
    private var restoreAttempted = false
    private var uncertainFastEnable = false
    private let backoff: [TimeInterval] = [5, 15, 45, 90]

    var nextDeadline: TimeInterval? {
        [pending?.expiresAt, retryAt, fastDeadline].compactMap { $0 }.min()
    }

    /// No persisted on/off switch may bypass the capability gate.
    mutating func enable(_ snapshot: RecoverySnapshot, now: TimeInterval, verifiedIntegration: Bool) -> RecoveryCommand? {
        // Never abandon a mode change whose delivery or restoration is pending.
        guard !needsNormalRestore, pending == nil else { return nil }
        guard verifiedIntegration else { pause(.unavailable); return nil }
        guard now.isFinite, now >= lastNow else { state = .paused(.clockChanged); return nil }
        target = snapshot.target; userRevision = snapshot.userRevision
        lastRevision = snapshot.revision; lastNow = now; lastSnapshot = nil
        attempts = 0; continues = 0; usedFast = false
        retryAt = nil; fastDeadline = nil; seenFailures.removeAll(); waitingFailure = nil
        returningToNormal = false; resumeAfterNormal = false; terminalAfterNormal = nil; restoreAttempted = false
        state = .watching
        return update(snapshot, now: now)
    }

    /// Disabling forbids further generation. Restore is a separate, verified,
    /// best-effort Normal-only action when safe state becomes available.
    mutating func disable() {
        if pending?.action == .setFast { uncertainFastEnable = true }
        pending = nil; retryAt = nil; waitingFailure = nil; fastDeadline = nil
        resumeAfterNormal = false; terminalAfterNormal = .off; state = .off
    }

    mutating func update(_ snapshot: RecoverySnapshot, now: TimeInterval) -> RecoveryCommand? {
        guard (state != .off && state != .finished) || needsNormalRestore else { return nil }
        guard validate(snapshot, now: now) else { return nil }
        if let reason = barrier(snapshot) {
            pause(reason)
            return restoreIfSafe(snapshot, now: now)
        }
        if case .paused = state { return restoreIfSafe(snapshot, now: now) }
        if state == .off { return restoreIfSafe(snapshot, now: now) }
        if let command = pending {
            if now >= command.expiresAt {
                pause(.unconfirmed)
                return restoreIfSafe(snapshot, now: now)
            }
            return nil
        }
        if returningToNormal { return restoreIfSafe(snapshot, now: now) }
        if let deadline = fastDeadline, now >= deadline {
            retryAt = nil; waitingFailure = nil; returningToNormal = true
            if snapshot.phase == .running {
                resumeAfterNormal = continues == 0 && attempts < backoff.count
                return issue(.stopForNormal, snapshot, now: now)
            }
            terminalAfterNormal = snapshot.phase == .completed ? .finished : .paused(.fastExpired)
            return restoreIfSafe(snapshot, now: now)
        }
        switch snapshot.phase {
        case .completed:
            retryAt = nil; waitingFailure = nil
            if needsNormalRestore {
                terminalAfterNormal = .finished; returningToNormal = true
                return restoreIfSafe(snapshot, now: now)
            }
            state = .finished; return nil
        case .interrupted:
            pause(.manualStop); return restoreIfSafe(snapshot, now: now)
        case .running:
            // An unsolicited run means the user/client took over. Receipt-based
            // adoption of a new turn happens only in confirm(), never here.
            if waitingFailure != nil { pause(.targetChanged); return restoreIfSafe(snapshot, now: now) }
            state = .watching; return nil
        case .failed: break
        }
        guard let failure = snapshot.failure, failure.kind == .capacity, !failure.id.isEmpty else {
            pause(.nonCapacity); return restoreIfSafe(snapshot, now: now)
        }
        if let delay = failure.retryNotBefore, !delay.isFinite {
            pause(.stale); return restoreIfSafe(snapshot, now: now)
        }
        if waitingFailure != failure.id {
            guard !seenFailures.contains(failure.id) else { return nil }
            guard attempts < backoff.count else { pause(.retryLimit); return restoreIfSafe(snapshot, now: now) }
            seenFailures.insert(failure.id); waitingFailure = failure.id
            retryAt = max(now + backoff[attempts], failure.retryNotBefore ?? now)
            state = .waiting
        } else if let delay = failure.retryNotBefore {
            retryAt = max(retryAt ?? now, delay) // Never shorten a server delay.
        }
        guard let due = retryAt, now >= due else { return nil }
        guard snapshot.canRetry || (snapshot.canContinue && continues == 0) else {
            pause(snapshot.canContinue ? .retryLimit : .unsupported)
            return restoreIfSafe(snapshot, now: now)
        }
        if snapshot.mode == .fast && !needsNormalRestore { return issue(.setNormal, snapshot, now: now) }
        if snapshot.mode == .normal && attempts >= 1 && !usedFast {
            fastBursts.removeAll { now - $0 >= 3600 }
            guard fastBursts.count < 2, fastBursts.last.map({ now - $0 >= 900 }) ?? true else {
                pause(.fastLimit); return nil
            }
            return issue(.setFast, snapshot, now: now)
        }
        return retry(snapshot, now: now)
    }

    /// Call only on an authoritative, causally correlated state change, not a
    /// successful click/RPC acknowledgement. A retry receipt names its new turn.
    mutating func confirm(_ id: UInt64, snapshot: RecoverySnapshot, now: TimeInterval) -> RecoveryCommand? {
        guard let command = pending, command.id == id else { return nil }
        guard now.isFinite, now >= lastNow, now < command.expiresAt,
              snapshot.isFresh(at: now), snapshot.revision > command.expected.revision,
              snapshot.revision >= lastRevision,
              snapshot.target.sameConversation(as: command.expected.target), snapshot.target.isValid else {
            pause(.unconfirmed); return nil
        }
        let mayAdoptTurn = command.action.startsTurn
        guard (mayAdoptTurn || snapshot.target == command.expected.target),
              snapshot.userRevision == userRevision else { pause(.targetChanged); return nil }
        // Check overrides BEFORE adopting a successful command's result.
        if let reason = barrier(snapshot), command.action != .setNormal {
            pause(reason); return restoreIfSafe(snapshot, now: now)
        }
        let confirmed: Bool
        switch command.action {
        case .setNormal: confirmed = snapshot.mode == .normal && snapshot.phase == command.expected.phase
        case .setFast: confirmed = snapshot.mode == .fast && snapshot.phase == .failed && snapshot.failure == command.expected.failure
        case .retry, .continueOnce: confirmed = snapshot.phase == .running && snapshot.mode == command.expected.mode
        case .stopForNormal: confirmed = snapshot.phase == .interrupted && !snapshot.manuallyStopped
        }
        guard confirmed else { pause(.unconfirmed); return nil }
        pending = nil; lastNow = now; lastRevision = snapshot.revision; lastSnapshot = snapshot; target = snapshot.target
        switch command.action {
        case .setFast:
            uncertainFastEnable = false
            state = .waiting
            return update(snapshot, now: now)
        case .setNormal:
            needsNormalRestore = false; fastDeadline = nil; restoreAttempted = false; uncertainFastEnable = false
            if let terminal = terminalAfterNormal {
                terminalAfterNormal = nil; returningToNormal = false; resumeAfterNormal = false
                state = terminal; return nil
            }
            if returningToNormal {
                returningToNormal = false
                guard resumeAfterNormal else { state = .finished; return nil }
                resumeAfterNormal = false
                if let reason = barrier(snapshot) { pause(reason); return nil }
                return issue(.continueOnce, snapshot, now: now)
            }
            state = .waiting
            return update(snapshot, now: now)
        case .retry, .continueOnce:
            retryAt = nil; waitingFailure = nil; state = .watching
            return update(snapshot, now: now)
        case .stopForNormal:
            return restoreIfSafe(snapshot, now: now)
        }
    }

    /// A rejected or uncertain side effect is never automatically repeated.
    mutating func unconfirmed(_ id: UInt64) {
        guard pending?.id == id else { return }
        pause(.unconfirmed)
    }

    private mutating func validate(_ snapshot: RecoverySnapshot, now: TimeInterval) -> Bool {
        guard now.isFinite, now >= lastNow else { pause(.clockChanged); return false }
        lastNow = now
        guard snapshot.isFresh(at: now), snapshot.revision >= lastRevision else { pause(.stale); return false }
        guard snapshot.target.isValid, snapshot.target == target, snapshot.userRevision == userRevision else {
            pause(.targetChanged); return false
        }
        if var previous = lastSnapshot, snapshot.revision == previous.revision {
            previous.observedAt = snapshot.observedAt
            guard previous == snapshot else { pause(.stale); return false }
        }
        lastRevision = snapshot.revision; lastSnapshot = snapshot
        return true
    }

    private func barrier(_ snapshot: RecoverySnapshot) -> RecoveryPause? {
        if snapshot.manuallyStopped { return .manualStop }
        if snapshot.cutoffBlocked { return .cutoff }
        if !snapshot.quotaFresh { return .quotaStale }
        if snapshot.hasDraft { return .draft }
        if snapshot.needsApproval { return .approval }
        if snapshot.mode == .unknown { return .unsupported }
        return nil
    }

    private mutating func pause(_ reason: RecoveryPause) {
        if pending?.action == .setFast { uncertainFastEnable = true }
        state = .paused(reason); pending = nil; retryAt = nil; waitingFailure = nil
        resumeAfterNormal = false; terminalAfterNormal = state; fastDeadline = nil
    }

    private mutating func retry(_ snapshot: RecoverySnapshot, now: TimeInterval) -> RecoveryCommand? {
        if snapshot.canRetry { return issue(.retry, snapshot, now: now) }
        return issue(.continueOnce, snapshot, now: now)
    }

    private mutating func issue(_ action: RecoveryAction, _ snapshot: RecoverySnapshot, now: TimeInterval) -> RecoveryCommand? {
        if action.startsTurn {
            guard attempts < backoff.count, action != .continueOnce || continues == 0 else {
                pause(.retryLimit); return restoreIfSafe(snapshot, now: now)
            }
        }
        let command = RecoveryCommand(id: nextID + 1, action: action, expected: snapshot, expiresAt: now + 5)
        guard command.permits(snapshot, now: now) else { pause(.unsupported); return nil }
        nextID += 1; pending = command; state = .awaitingConfirmation
        if action.startsTurn { attempts += 1; if action == .continueOnce { continues += 1 } }
        if action == .setFast {
            // Charge the burst and start the deadline BEFORE dispatch. An
            // uncertain enable must not permit another burst or lose cleanup.
            usedFast = true; fastBursts.append(now); fastDeadline = now + 120
            needsNormalRestore = true
        }
        if action == .setNormal && needsNormalRestore { restoreAttempted = true }
        return command
    }

    private mutating func restoreIfSafe(_ snapshot: RecoverySnapshot, now: TimeInterval) -> RecoveryCommand? {
        guard needsNormalRestore else { return nil }
        guard snapshot.target == target, snapshot.userRevision == userRevision,
              snapshot.isFresh(at: now), !snapshot.hasDraft, !snapshot.needsApproval else { return nil }
        if snapshot.mode == .normal && !uncertainFastEnable {
            needsNormalRestore = false; fastDeadline = nil; returningToNormal = false
            resumeAfterNormal = false
            if let terminal = terminalAfterNormal { state = terminal; terminalAfterNormal = nil }
            return nil
        }
        guard snapshot.mode != .unknown, !restoreAttempted else { return nil }
        return issue(.setNormal, snapshot, now: now)
    }
}

/// Production capability is intentionally closed. No AX permission, global
/// config edit, second engine, screen polling or hidden socket can open it.
enum DesktopRecoveryAvailability {
    static let canEnable = false
    static let title = "Auto-recover · unavailable"
    static let detail = "Codex controls are unverified. Live recovery is off."
}
