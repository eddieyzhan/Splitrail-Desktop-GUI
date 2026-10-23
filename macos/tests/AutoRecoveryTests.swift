import Foundation

@main struct AutoRecoveryTests {
    static func main() {
        var report = RecoverySimulation.run()
        func test(_ name: String, _ body: () throws -> Void) {
            do { try body(); report.passed.append(name) }
            catch { report.failed.append("\(name): \(error)") }
        }

        for (name, edit, reason) in [
            ("manual Stop", { (s: inout RecoverySnapshot) in s.manuallyStopped = true }, RecoveryPause.manualStop),
            ("cutoff", { (s: inout RecoverySnapshot) in s.cutoffBlocked = true }, .cutoff),
            ("stale quota", { (s: inout RecoverySnapshot) in s.quotaFresh = false }, .quotaStale),
            ("draft", { (s: inout RecoverySnapshot) in s.hasDraft = true }, .draft),
            ("approval", { (s: inout RecoverySnapshot) in s.needsApproval = true }, .approval)
        ] {
            test("\(name) blocks initial enable") {
                var f = RecoveryFixture(); edit(&f.snapshot)
                try recoveryExpect(f.start() == nil && f.engine.state == .paused(reason), "Override not respected")
                try recoveryExpect(f.engine.nextDeadline == nil && f.actions.isEmpty, "Blocked start schedules work")
            }
            test("\(name) wins at the retry deadline") {
                var f = RecoveryFixture(); _ = f.start()
                f.now += 5
                _ = f.change(edit)
                try recoveryExpect(f.actions.isEmpty && f.engine.state == .paused(reason), "Retry won the race")
                _ = f.change { s in s.manuallyStopped = false; s.cutoffBlocked = false; s.quotaFresh = true; s.hasDraft = false; s.needsApproval = false }
                try recoveryExpect(f.advance(100) == nil, "Clearing an override silently resumed work")
            }
            test("\(name) wins after dispatch but before confirmation") {
                var f = RecoveryFixture(); _ = f.start()
                let retry = try recoveryRequire(f.advance(5), .retry)
                edit(&f.snapshot); f.snapshot.revision += 1
                f.snapshot.phase = .running
                let result = f.engine.confirm(retry.id, snapshot: f.snapshot, now: f.now)
                try recoveryExpect(result == nil && f.engine.state == .paused(reason), "Confirmation ignored override")
                try recoveryExpect(f.engine.nextDeadline == nil, "Pending retry was retained")
            }
            test("\(name) cannot be bypassed at Fast expiry") {
                var f = RecoveryFixture(); try f.enterFast()
                f.now = f.engine.fastDeadline!
                let result = f.change(edit)
                try recoveryExpect(result == nil || result?.action == .setNormal, "Override emitted generation or Stop")
                if let result { _ = try f.acknowledge(result) }
                try recoveryExpect(f.engine.state == .paused(reason), "Fast cleanup erased override")
            }
        }

        for kind in [RecoveryFailureKind.quota, .authentication, .network, .other] {
            test("Non-capacity \(kind) cannot trigger recovery") {
                var f = RecoveryFixture(); f.snapshot.failure?.kind = kind
                _ = f.start()
                try recoveryExpect(f.engine.state == .paused(.nonCapacity), "Non-capacity error retried")
                try recoveryExpect(f.advance(9999) == nil && f.actions.isEmpty, "Non-capacity error escalated")
            }
        }
        for (name, edit) in [
            ("application restart", { (s: inout RecoverySnapshot) in s.target.applicationInstance = "new-process" }),
            ("thread switch", { (s: inout RecoverySnapshot) in s.target.thread = "other-thread" }),
            ("new user turn", { (s: inout RecoverySnapshot) in s.target.turn = "user-turn" }),
            ("user edit", { (s: inout RecoverySnapshot) in s.userRevision += 1 })
        ] {
            test("\(name) invalidates queued action") {
                var f = RecoveryFixture(); _ = f.start()
                let command = try recoveryRequire(f.advance(5), .retry)
                edit(&f.snapshot)
                try recoveryExpect(!command.permits(f.snapshot, now: f.now), "Stale target dispatch allowed")
                f.snapshot.revision += 1
                try recoveryExpect(f.engine.update(f.snapshot, now: f.now) == nil, "Changed target got action")
                try recoveryExpect(f.engine.state == .paused(.targetChanged), "Target change not latched")
            }
        }
        test("Preflight rejects a newly typed draft without a revision update") {
            var f = RecoveryFixture(); _ = f.start()
            let command = try recoveryRequire(f.advance(5), .retry)
            f.snapshot.hasDraft = true
            try recoveryExpect(!command.permits(f.snapshot, now: f.now), "Draft overwritten at dispatch")
        }
        test("Preflight rejects an approval without a revision update") {
            var f = RecoveryFixture(); _ = f.start()
            let command = try recoveryRequire(f.advance(5), .retry)
            f.snapshot.needsApproval = true
            try recoveryExpect(!command.permits(f.snapshot, now: f.now), "Approval bypassed at dispatch")
        }
        test("Preflight rejects a late command") {
            var f = RecoveryFixture(); _ = f.start()
            let command = try recoveryRequire(f.advance(5), .retry)
            f.snapshot.observedAt = command.expiresAt
            try recoveryExpect(!command.permits(f.snapshot, now: command.expiresAt), "Expired command applied")
        }
        test("Preflight rejects stale state") {
            var f = RecoveryFixture(); _ = f.start()
            let command = try recoveryRequire(f.advance(5), .retry)
            try recoveryExpect(!command.permits(f.snapshot, now: f.now + 3), "Stale dispatch accepted")
        }
        test("An earlier mode confirmation cannot confirm another action") {
            var f = RecoveryFixture(); _ = f.start()
            let command = try recoveryRequire(f.advance(5), .retry)
            try recoveryExpect(f.engine.confirm(command.id + 99, snapshot: f.snapshot, now: f.now) == nil, "Mismatched receipt accepted")
            try recoveryExpect(f.engine.pending?.id == command.id, "Wrong receipt consumed pending action")
        }
        test("An acknowledgement with unchanged revision is not confirmation") {
            var f = RecoveryFixture(); _ = f.start()
            let command = try recoveryRequire(f.advance(5), .retry)
            f.snapshot.phase = .running
            try recoveryExpect(f.engine.confirm(command.id, snapshot: f.snapshot, now: f.now) == nil, "Ack treated as state change")
            try recoveryExpect(f.engine.state == .paused(.unconfirmed), "Unconfirmed operation did not latch")
        }
        test("Mode toggle must actually change to Normal") {
            var f = RecoveryFixture(); f.snapshot.mode = .fast; _ = f.start()
            let normal = try recoveryRequire(f.advance(5), .setNormal)
            f.snapshot.revision += 1
            try recoveryExpect(f.engine.confirm(normal.id, snapshot: f.snapshot, now: f.now) == nil, "Retried on wrong mode")
            try recoveryExpect(f.engine.attempts == 0, "Unverified mode allowed generation")
        }
        test("Fast enable must actually change to Fast") {
            var f = RecoveryFixture(); _ = f.start()
            _ = try f.acknowledge(try recoveryRequire(f.advance(5), .retry))
            _ = f.capacity("again")
            let fast = try recoveryRequire(f.advance(15), .setFast)
            f.snapshot.revision += 1
            try recoveryExpect(f.engine.confirm(fast.id, snapshot: f.snapshot, now: f.now) == nil, "Unverified Fast allowed retry")
            try recoveryExpect(f.engine.needsNormalRestore, "Ambiguous enable lost cleanup debt")
            let restore = try recoveryRequire(f.advance(1), .setNormal)
            _ = try f.acknowledge(restore)
            try recoveryExpect(!f.engine.needsNormalRestore && f.engine.state == .paused(.unconfirmed), "Cleanup resumed uncertain operation")
        }
        test("Unknown Stop outcome does not change mode or continue") {
            var f = RecoveryFixture(); try f.enterFast()
            let stop = try recoveryRequire(f.advance(f.engine.fastDeadline! - f.now), .stopForNormal)
            f.engine.unconfirmed(stop.id)
            // Best-effort mode restoration remains allowed, but never Continue.
            let normal = try recoveryRequire(f.advance(1), .setNormal)
            _ = try f.acknowledge(normal)
            try recoveryExpect(f.engine.continues == 0 && f.engine.state == .paused(.unconfirmed), "Unconfirmed Stop resumed work")
        }
        test("No second automatic Normal attempt after ambiguous restoration") {
            var f = RecoveryFixture(); try f.enterFast()
            let normal = try recoveryRequire(f.change { $0.phase = .completed }, .setNormal)
            f.engine.unconfirmed(normal.id)
            try recoveryExpect(f.advance(999) == nil && f.engine.needsNormalRestore, "Repeated uncertain mode mutation")
        }
        test("Late retry receipt cannot reopen a paused episode") {
            var f = RecoveryFixture(); _ = f.start()
            let retry = try recoveryRequire(f.advance(5), .retry)
            _ = f.advance(5)
            f.snapshot.revision += 1; f.snapshot.phase = .running
            try recoveryExpect(f.engine.confirm(retry.id, snapshot: f.snapshot, now: f.now) == nil, "Late receipt reopened recovery")
            try recoveryExpect(f.engine.state == .paused(.unconfirmed), "Timeout did not latch")
        }
        test("Disable cancels a scheduled retry") {
            var f = RecoveryFixture(); _ = f.start(); f.engine.disable()
            try recoveryExpect(f.advance(100) == nil && f.engine.nextDeadline == nil, "Disabled recovery ran")
        }
        test("Disable during Fast restores Normal without continuing") {
            var f = RecoveryFixture(); try f.enterFast(); f.engine.disable()
            let normal = try recoveryRequire(f.advance(1), .setNormal)
            _ = try f.acknowledge(normal)
            try recoveryExpect(f.engine.state == .off && f.engine.continues == 0, "Disable restarted work")
        }
        test("Re-enabling cannot discard an outstanding Fast restoration") {
            var f = RecoveryFixture(); try f.enterFast()
            let deadline = f.engine.fastDeadline
            try recoveryExpect(f.start() == nil && f.engine.fastDeadline == deadline, "Enable abandoned a burst")
        }
        test("Duplicate failures never dispatch twice") {
            var f = RecoveryFixture(); _ = f.start()
            let retry = try recoveryRequire(f.advance(5), .retry)
            let oldFailure = f.snapshot.failure!
            _ = try f.acknowledge(retry)
            _ = f.change { $0.phase = .failed; $0.failure = oldFailure }
            for _ in 0..<100 { try recoveryExpect(f.advance(10) == nil, "Duplicate failure retried") }
            try recoveryExpect(f.engine.attempts == 1, "Duplicate accounting changed")
        }
        test("Server can extend but not shorten a scheduled retry") {
            var f = RecoveryFixture(); _ = f.start()
            _ = f.change { $0.failure?.retryNotBefore = 1100 }
            _ = f.change { $0.failure?.retryNotBefore = 1001 }
            try recoveryExpect(f.engine.retryAt == 1100, "Server delay shortened")
            try recoveryExpect(f.advance(99) == nil, "Early retry")
            _ = try recoveryRequire(f.advance(1), .retry)
        }
        test("Very long Retry-After never gets silently capped") {
            var f = RecoveryFixture(); f.snapshot.failure?.retryNotBefore = f.now + 86400
            _ = f.start()
            try recoveryExpect(f.advance(3600) == nil, "Long server delay capped")
            try recoveryExpect(f.engine.retryAt == 87400, "Long server delay lost")
        }
        test("Fast deadline outranks a longer server delay") {
            var f = RecoveryFixture(); try f.enterFast()
            _ = f.capacity("long-delay", retryAfter: 600)
            let normal = try recoveryRequire(f.advance(f.engine.fastDeadline! - f.now), .setNormal)
            _ = try f.acknowledge(normal)
            try recoveryExpect(f.engine.attempts == 2 && f.engine.state == .paused(.fastExpired), "Fast timeout retried too early")
        }
        test("A sleep-sized clock jump expires Fast immediately") {
            var f = RecoveryFixture(); try f.enterFast()
            let stop = try recoveryRequire(f.advance(3600), .stopForNormal)
            try recoveryExpect(stop.expected.target == f.snapshot.target, "Wake targeted wrong turn")
        }
        test("Backoff grows from 5 to 15 to 45 seconds") {
            var f = RecoveryFixture(); _ = f.start()
            try recoveryExpect(f.engine.retryAt == f.now + 5, "Initial delay wrong")
            _ = try f.acknowledge(try recoveryRequire(f.advance(5), .retry))
            _ = f.capacity("two")
            try recoveryExpect(f.engine.retryAt == f.now + 15, "Second delay wrong")
            let fast = try recoveryRequire(f.advance(15), .setFast)
            _ = try f.acknowledge(try recoveryRequire(try f.acknowledge(fast), .retry))
            _ = f.capacity("three")
            try recoveryExpect(f.engine.retryAt == f.now + 45, "Third delay wrong")
        }
        test("Fast deadline does not extend on repeated errors") {
            var f = RecoveryFixture(); try f.enterFast()
            let deadline = f.engine.fastDeadline
            _ = f.capacity("three")
            _ = try f.acknowledge(try recoveryRequire(f.advance(45), .retry))
            _ = f.capacity("four")
            try recoveryExpect(f.engine.fastDeadline == deadline, "Burst extended after another error")
            try recoveryExpect(f.engine.retryAt == f.now + 90, "Fourth delay wrong")
        }
        test("Four generation attempts is a hard upper bound") {
            var f = RecoveryFixture(); try f.enterFast()
            _ = f.capacity("three")
            _ = try f.acknowledge(try recoveryRequire(f.advance(45), .retry))
            let stop = try recoveryRequire(f.advance(f.engine.fastDeadline! - f.now), .stopForNormal)
            let normal = try recoveryRequire(try f.acknowledge(stop), .setNormal)
            _ = try f.acknowledge(try recoveryRequire(try f.acknowledge(normal), .continueOnce))
            try recoveryExpect(f.engine.attempts == 4, "Scenario did not spend four attempts")
            _ = f.capacity("fifth")
            try recoveryExpect(f.engine.state == .paused(.retryLimit) && f.advance(1000) == nil, "Fifth attempt permitted")
        }
        test("Fast bursts are separated by at least 15 minutes") {
            var f = RecoveryFixture(); try f.enterFast()
            _ = try f.acknowledge(try recoveryRequire(f.change { $0.phase = .completed }, .setNormal))
            f.snapshot.phase = .failed; f.snapshot.failure = RecoveryFailure(id: "new-episode", kind: .capacity)
            f.snapshot.revision += 1; _ = f.start()
            _ = try f.acknowledge(try recoveryRequire(f.advance(5), .retry))
            _ = f.capacity("new-capacity")
            try recoveryExpect(f.advance(15) == nil && f.engine.state == .paused(.fastLimit), "Fast cooldown bypassed")
        }
        test("Two bursts per rolling hour survives explicit re-enables") {
            var f = RecoveryFixture()
            for episode in 0..<3 {
                f.now = 1000 + Double(episode) * 1000
                f.snapshot.observedAt = f.now; f.snapshot.revision += 1
                f.snapshot.phase = .failed; f.snapshot.mode = .normal
                f.snapshot.failure = RecoveryFailure(id: "episode-\(episode)", kind: .capacity)
                _ = f.start()
                _ = try f.acknowledge(try recoveryRequire(f.advance(5), .retry))
                _ = f.capacity("escalate-\(episode)")
                let command = f.advance(15)
                if episode < 2 {
                    let fast = try recoveryRequire(command, .setFast)
                    _ = try f.acknowledge(try recoveryRequire(try f.acknowledge(fast), .retry))
                    _ = try f.acknowledge(try recoveryRequire(f.change { $0.phase = .completed }, .setNormal))
                } else {
                    try recoveryExpect(command == nil && f.engine.state == .paused(.fastLimit), "Third hourly burst permitted")
                }
            }
        }
        test("Unknown mode is never toggled blindly") {
            var f = RecoveryFixture(); f.snapshot.mode = .unknown; _ = f.start()
            try recoveryExpect(f.engine.state == .paused(.unsupported) && f.advance(500) == nil, "Unknown mode toggled")
        }
        test("Missing Stop control forbids timeout continuation") {
            var f = RecoveryFixture(); try f.enterFast(); f.snapshot.canStop = false; f.snapshot.revision += 1
            try recoveryExpect(f.advance(f.engine.fastDeadline! - f.now) == nil, "No Stop control still continued")
            try recoveryExpect(f.engine.continues == 0 && f.engine.needsNormalRestore, "Missing Stop lost restore debt")
        }
        test("No Retry or Continue means no Fast escalation") {
            var f = RecoveryFixture(); f.snapshot.canRetry = false; f.snapshot.canContinue = false; _ = f.start()
            try recoveryExpect(f.advance(5) == nil && f.actions.isEmpty, "Changed mode with no retry path")
        }
        for value in [Double.nan, Double.infinity, -Double.infinity] {
            test("Non-finite Retry-After \(value) is rejected") {
                var f = RecoveryFixture(); f.snapshot.failure?.retryNotBefore = value; _ = f.start()
                try recoveryExpect(f.engine.state == .paused(.stale) && f.engine.nextDeadline == nil, "Invalid deadline scheduled")
            }
        }
        test("Clock rollback fails closed") {
            var f = RecoveryFixture(); _ = f.start()
            try recoveryExpect(f.advance(-1) == nil && f.engine.state == .paused(.clockChanged), "Clock rollback allowed work")
        }
        test("Stale snapshots fail closed") {
            var f = RecoveryFixture(); f.snapshot.observedAt -= 3
            try recoveryExpect(f.start() == nil && f.engine.state == .paused(.stale), "Stale snapshot enabled")
        }
        test("Contradictory state with identical revision fails closed") {
            var f = RecoveryFixture(); _ = f.start(); f.snapshot.mode = .fast
            try recoveryExpect(f.advance(5) == nil && f.engine.state == .paused(.stale), "Same revision changed meaning")
        }
        test("Old notifications do not rewind state") {
            var f = RecoveryFixture(); _ = f.start(); f.snapshot.revision = 0
            try recoveryExpect(f.advance(5) == nil && f.engine.state == .paused(.stale), "Out-of-order notification accepted")
        }
        test("A late confirmation cannot rewind a newer observation") {
            var f = RecoveryFixture(); _ = f.start()
            let retry = try recoveryRequire(f.advance(5), .retry)
            _ = f.change { $0.revision += 10 }
            var old = f.snapshot; old.revision = retry.expected.revision + 1; old.phase = .running
            try recoveryExpect(f.engine.confirm(retry.id, snapshot: old, now: f.now) == nil, "Old confirmation accepted")
            try recoveryExpect(f.engine.state == .paused(.unconfirmed), "Old confirmation rewound policy")
        }
        test("Capability loss clears an already scheduled retry") {
            var f = RecoveryFixture(); _ = f.start()
            _ = f.start(verified: false)
            try recoveryExpect(f.engine.nextDeadline == nil && f.advance(60) == nil, "Unavailable integration retained work")
        }
        test("A completed episode needs explicit re-enabling") {
            var f = RecoveryFixture(); f.snapshot.phase = .running; f.snapshot.failure = nil; _ = f.start()
            _ = f.change { $0.phase = .completed }
            _ = f.capacity("unexpected-late-error")
            try recoveryExpect(f.advance(60) == nil && f.actions.isEmpty, "Completed turn resumed without enable")
        }
        test("10,000 idle notifications never create work") {
            var f = RecoveryFixture(); f.snapshot.phase = .running; f.snapshot.failure = nil; _ = f.start()
            for _ in 0..<10_000 { try recoveryExpect(f.advance(1) == nil, "Idle event generated an action") }
            try recoveryExpect(f.engine.nextDeadline == nil && f.actions.isEmpty, "Idle policy scheduled polling")
        }

        print(report.detail)
        print("\(report.passed.count) passed; \(report.failed.count) failed. Synthetic tests only; no live Codex controls accessed.")
        exit(report.success ? 0 : 1)
    }
}
