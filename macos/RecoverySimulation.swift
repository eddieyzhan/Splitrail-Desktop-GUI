import Foundation

struct RecoveryTestFailure: Error, CustomStringConvertible {
    let description: String
}

/// Synthetic adapter used by the in-app diagnostic AND the standalone tests.
/// It cannot reach a real application. No system permission or network is used.
struct RecoveryFixture {
    var engine = AutoRecovery()
    var now: TimeInterval = 1_000
    var snapshot = RecoverySnapshot(
        target: RecoveryTarget(applicationInstance: "synthetic-app", thread: "synthetic-thread", turn: "turn-1"),
        revision: 1, observedAt: 1_000, phase: .failed, mode: .normal,
        failure: RecoveryFailure(id: "capacity-1", kind: .capacity, retryNotBefore: nil))
    private(set) var actions: [RecoveryAction] = []

    mutating func start(verified: Bool = true) -> RecoveryCommand? {
        record(engine.enable(snapshot, now: now, verifiedIntegration: verified))
    }
    mutating func advance(_ seconds: TimeInterval) -> RecoveryCommand? {
        now += seconds; snapshot.observedAt = now
        return record(engine.update(snapshot, now: now))
    }
    mutating func change(_ edit: (inout RecoverySnapshot) -> Void) -> RecoveryCommand? {
        edit(&snapshot); snapshot.revision += 1; snapshot.observedAt = now
        return record(engine.update(snapshot, now: now))
    }
    mutating func acknowledge(_ command: RecoveryCommand) throws -> RecoveryCommand? {
        try recoveryExpect(command.permits(snapshot, now: now), "Synthetic dispatch violated preconditions")
        now += 0.01; snapshot.revision += 1; snapshot.observedAt = now
        switch command.action {
        case .setNormal: snapshot.mode = .normal
        case .setFast: snapshot.mode = .fast
        case .retry, .continueOnce:
            snapshot.phase = .running; snapshot.failure = nil
            snapshot.target.turn = "synthetic-turn-\(command.id)"
        case .stopForNormal: snapshot.phase = .interrupted
        }
        return record(engine.confirm(command.id, snapshot: snapshot, now: now))
    }
    mutating func capacity(_ id: String, retryAfter: TimeInterval? = nil) -> RecoveryCommand? {
        let deadline = retryAfter.map { now + $0 }
        return change { value in
            value.phase = .failed
            value.failure = RecoveryFailure(id: id, kind: .capacity, retryNotBefore: deadline)
        }
    }
    mutating func enterFast() throws {
        _ = start()
        let normal = try recoveryRequire(advance(5), .retry)
        _ = try acknowledge(normal)
        _ = capacity("capacity-2")
        let fast = try recoveryRequire(advance(15), .setFast)
        let retry = try recoveryRequire(try acknowledge(fast), .retry)
        _ = try acknowledge(retry)
    }
    private mutating func record(_ command: RecoveryCommand?) -> RecoveryCommand? {
        if let command { actions.append(command.action) }
        return command
    }
}

func recoveryExpect(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {
    if try condition() == false { throw RecoveryTestFailure(description: message) }
}
func recoveryRequire(_ command: RecoveryCommand?, _ action: RecoveryAction) throws -> RecoveryCommand {
    guard let command, command.action == action else {
        throw RecoveryTestFailure(description: "Expected \(action), got \(String(describing: command?.action))")
    }
    return command
}

struct RecoveryTestReport {
    var passed: [String] = []
    var failed: [String] = []
    var success: Bool { failed.isEmpty && !passed.isEmpty }
    var summary: String {
        success ? "\(passed.count) simulated checks passed. Live Codex untested."
            : "\(failed.count) simulated checks failed. Live recovery stays off."
    }
    var detail: String { (passed.map { "PASS · \($0)" } + failed.map { "FAIL · \($0)" }).joined(separator: "\n") }
}

enum RecoverySimulation {
    static func run() -> RecoveryTestReport {
        var report = RecoveryTestReport()
        func test(_ name: String, _ body: () throws -> Void) {
            do { try body(); report.passed.append(name) }
            catch { report.failed.append("\(name): \(error)") }
        }
        test("Live integration stays disabled") {
            var fixture = RecoveryFixture()
            try recoveryExpect(!DesktopRecoveryAvailability.canEnable, "Live adapter must remain unavailable")
            try recoveryExpect(fixture.start(verified: DesktopRecoveryAvailability.canEnable) == nil, "Unverified integration ran")
            try recoveryExpect(fixture.engine.state == .paused(.unavailable), "Missing unavailable state")
            try recoveryExpect(fixture.engine.nextDeadline == nil, "Disabled feature scheduled work")
        }
        test("Server retry delay is respected") {
            var fixture = RecoveryFixture()
            fixture.snapshot.failure?.retryNotBefore = fixture.now + 60
            _ = fixture.start()
            try recoveryExpect(fixture.advance(59) == nil, "Retried before Retry-After")
            _ = try recoveryRequire(fixture.advance(1), .retry)
        }
        test("Existing Fast switches to Normal before retry") {
            var fixture = RecoveryFixture(); fixture.snapshot.mode = .fast
            _ = fixture.start()
            let normal = try recoveryRequire(fixture.advance(5), .setNormal)
            try recoveryExpect(fixture.engine.attempts == 0, "Retried before mode verification")
            _ = try recoveryRequire(try fixture.acknowledge(normal), .retry)
        }
        test("Normal failure enables one temporary Fast burst") {
            var fixture = RecoveryFixture(); try fixture.enterFast()
            try recoveryExpect(fixture.actions == [.retry, .setFast, .retry], "Wrong escalation sequence")
            try recoveryExpect(fixture.engine.fastDeadline != nil && fixture.engine.needsNormalRestore, "Fast has no cleanup deadline")
        }
        test("Two-minute expiry stops, verifies Normal, then continues") {
            var fixture = RecoveryFixture(); try fixture.enterFast()
            let remaining = fixture.engine.fastDeadline! - fixture.now
            try recoveryExpect(fixture.advance(remaining - 0.1) == nil, "Fast expired early")
            let stop = try recoveryRequire(fixture.advance(0.1), .stopForNormal)
            let normal = try recoveryRequire(try fixture.acknowledge(stop), .setNormal)
            let resume = try recoveryRequire(try fixture.acknowledge(normal), .continueOnce)
            _ = try fixture.acknowledge(resume)
            try recoveryExpect(fixture.snapshot.mode == .normal && fixture.snapshot.phase == .running, "Normal continuation failed")
            try recoveryExpect(!fixture.engine.needsNormalRestore && fixture.engine.fastDeadline == nil, "Cleanup debt remained")
        }
        test("Manual Stop forbids further generation") {
            var fixture = RecoveryFixture(); try fixture.enterFast()
            let normal = try recoveryRequire(fixture.change { $0.manuallyStopped = true; $0.phase = .interrupted }, .setNormal)
            try recoveryExpect(try fixture.acknowledge(normal) == nil, "Manual Stop was overridden")
            try recoveryExpect(fixture.engine.state == .paused(.manualStop), "Manual Stop did not latch")
            try recoveryExpect(fixture.advance(600) == nil, "Manual Stop restarted later")
        }
        test("Usage cutoff cancels waiting retries") {
            var fixture = RecoveryFixture(); _ = fixture.start()
            _ = fixture.change { $0.cutoffBlocked = true }
            try recoveryExpect(fixture.advance(600) == nil && fixture.actions.isEmpty, "Cutoff allowed retry")
            try recoveryExpect(fixture.engine.state == .paused(.cutoff), "Cutoff did not latch")
        }
        test("Draft and approval requests are untouched") {
            for draft in [true, false] {
                var fixture = RecoveryFixture(); try fixture.enterFast()
                let count = fixture.actions.count
                _ = fixture.change { if draft { $0.hasDraft = true } else { $0.needsApproval = true } }
                try recoveryExpect(fixture.advance(180) == nil && fixture.actions.count == count, "Edited a draft or approval")
                try recoveryExpect(fixture.engine.needsNormalRestore, "Lost pending Normal restoration")
            }
        }
        test("Unknown delivery never repeats a generation") {
            var fixture = RecoveryFixture(); _ = fixture.start()
            let retry = try recoveryRequire(fixture.advance(5), .retry)
            fixture.engine.unconfirmed(retry.id)
            try recoveryExpect(fixture.advance(600) == nil && fixture.engine.attempts == 1, "Uncertain retry repeated")
        }
        test("A changed conversation receives no action") {
            var fixture = RecoveryFixture(); _ = fixture.start()
            _ = fixture.change { $0.target.thread = "another-conversation" }
            try recoveryExpect(fixture.advance(600) == nil && fixture.actions.isEmpty, "Wrong conversation targeted")
        }
        test("Continue fallback is used at most once") {
            var fixture = RecoveryFixture(); fixture.snapshot.canRetry = false
            _ = fixture.start()
            let first = try recoveryRequire(fixture.advance(5), .continueOnce)
            _ = try fixture.acknowledge(first)
            _ = fixture.capacity("next-capacity")
            try recoveryExpect(fixture.advance(15) == nil, "Spent Continue budget still changed modes")
            try recoveryExpect(fixture.engine.continues == 1 && fixture.engine.attempts == 1, "Continue budget exceeded")
        }
        test("Completed Fast work restores Normal early") {
            var fixture = RecoveryFixture(); try fixture.enterFast()
            let normal = try recoveryRequire(fixture.change { $0.phase = .completed }, .setNormal)
            _ = try fixture.acknowledge(normal)
            try recoveryExpect(fixture.engine.state == .finished && !fixture.engine.needsNormalRestore, "Successful work left Fast enabled")
        }
        return report
    }
}
