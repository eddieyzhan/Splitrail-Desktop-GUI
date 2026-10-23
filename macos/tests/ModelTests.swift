import Foundation

@main struct ModelTests {
    static func main() throws {
        let now = 1_800_000_000.0
        func reading(_ used: Double, stamp: Double = 1_800_000_000, reset: Double = 1_800_500_000) -> QuotaReading {
            QuotaReading(timestamp: stamp, status: "fresh", windows: [QuotaWindow(id: "weekly", label: "Weekly", kind: "weekly", percent: used, reset: reset)], expiries: [])
        }
        var guardState = NativeGuard()
        guardState.currentLimit = 80; guardState.resetLimit = 5
        try guardState.arm(reading(79), now: now)
        assert(!guardState.blocked && guardState.armed)
        guardState.observe(reading(81, stamp: now + 1), now: now + 1)
        assert(guardState.blocked)
        guardState.observe(reading(0, stamp: now + 2), now: now + 2)
        assert(guardState.blocked, "A cutoff cannot silently reopen headroom")
        guardState.disarm()
        try guardState.arm(reading(79), now: now)
        guardState.observe(reading(4, stamp: now + 1, reset: now + 700_000), now: now + 1)
        assert(guardState.afterReset && !guardState.blocked && guardState.limit == 5)
        guardState.observe(reading(5, stamp: now + 2, reset: now + 700_000), now: now + 2)
        assert(guardState.blocked)
        guardState.disarm()
        try guardState.arm(reading(2), now: now)
        guardState.observe(reading(99, stamp: now - 1), now: now + 20)
        assert(!guardState.blocked && guardState.previous!.timestamp == now, "Ignore out-of-order readings")
        guardState.checkAge(now: now + 121)
        assert(guardState.blocked)
        guardState.disarm()
        do { try guardState.arm(reading(0, stamp: now - 121), now: now); fatalError("Stale arming accepted") } catch {}
        guardState.currentLimit = .nan
        do { try guardState.arm(reading(0), now: now); fatalError("NaN accepted") } catch {}
        let days = [UsageDay(day: "2026-10-01", cost: 3, tokens: 12_000, conversations: 2,
                             models: [UsageModel(name: "gpt-6-sol", cost: 1, tokens: 4000), UsageModel(name: "gpt-6.1-sol", cost: 2, tokens: 8000)]),
                    UsageDay(day: "2026-09-30", cost: 99, tokens: 99, conversations: 99, models: [])]
        let total = Totals(days: days, start: "2026-10-01", end: "2026-10-31")
        assert(total.cost == 3 && total.tokens == 12_000 && total.conversations == 2)
        assert(total.models["Sol"]!.cost == 3 && total.models["Sol"]!.names.count == 2)
        assert(shortModel("gpt-6-astra") == "Astra")
        assert(shortModel("gpt-6.1-sol") == "Sol")
        assert(compact(1_000) == "1k" && compact(41_210_000) == "41.2M")
        let decoded = try JSONDecoder().decode(NativeCache.self, from: JSONEncoder().encode(NativeCache(days: days, timestamp: now, quota: reading(8))))
        assert(decoded.days.count == 2 && decoded.quota!.isFresh(at: now))
        print("Native model tests passed: cutoff thresholds/reset/staleness/order, date filtering, totals, formatting, cache round-trip")
    }
}
