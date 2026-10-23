import Foundation

protocol NativeActions: AnyObject {
    var cache: NativeCache { get }
    var syncInfo: [String: Any] { get }
    var preferences: [String: Any] { get }
    var guardState: NativeGuard { get }
    var message: String { get }
    var busy: Bool { get }
    var page: Int { get set }
    var rangePreset: Int { get set }
    var selectedRange: (String, String)? { get set }
    func action(_ name: String, options: [String: Any])
}

let nativeVersion = "1.3.0"

struct UsageModel: Codable {
    var name: String
    var cost: Double
    var tokens: Int64
}

struct UsageDay: Codable {
    var day: String
    var cost: Double
    var tokens: Int64
    var conversations: Int64
    var models: [UsageModel]
}

struct QuotaWindow: Codable {
    var id: String
    var label: String
    var kind: String
    var percent: Double?
    var reset: Double?
}

struct QuotaReading: Codable {
    var timestamp: Double
    var status: String
    var windows: [QuotaWindow]
    var expiries: [Double]
    var expiryStatus: String? = nil
    var expiryTimestamp: Double? = nil
    var expiryDetailsComplete: Bool? = nil
    var weekly: QuotaWindow? { windows.first { $0.kind == "weekly" } }
    func isFresh(at now: Double) -> Bool {
        guard status == "fresh", let percent = weekly?.percent, let reset = weekly?.reset else { return false }
        return percent.isFinite && (0...100).contains(percent) && reset > now && (-5...120).contains(now - timestamp)
    }
}

struct NativeCache: Codable {
    var days: [UsageDay] = []
    var timestamp: Double = 0
    var quota: QuotaReading?
    var notices: [String]?
}

struct Totals {
    var cost: Double = 0
    var tokens: Int64 = 0
    var conversations: Int64 = 0
    var models: [String: (cost: Double, names: Set<String>)] = [:]
    init(days: [UsageDay], start: String, end: String) {
        for day in days where day.day >= start && day.day <= end {
            cost += day.cost
            tokens += day.tokens
            conversations += day.conversations
            for model in day.models {
                let key = shortModel(model.name)
                var entry = models[key] ?? (0, [])
                entry.cost += model.cost
                entry.names.insert(model.name)
                models[key] = entry
            }
        }
    }
}

func shortModel(_ value: String) -> String {
    let name = value.lowercased()
    for family in ["astra", "sol", "luna"] {
        if name.split(separator: "-").contains(Substring(family)) { return family.capitalized }
    }
    return value.replacingOccurrences(of: "anthropic/", with: "")
        .replacingOccurrences(of: "openai/", with: "")
        .replacingOccurrences(of: "claude-", with: "")
        .replacingOccurrences(of: "gpt-", with: "")
}

func compact(_ value: Int64) -> String {
    for (divisor, suffix) in [(1_000_000_000.0, "B"), (1_000_000.0, "M"), (1_000.0, "k")] {
        if Double(value) >= divisor {
            let n = Double(value) / divisor
            return String(format: n >= 100 ? "%.0f%@" : "%.1f%@", n, suffix).replacingOccurrences(of: ".0", with: "")
        }
    }
    return String(value)
}

func money(_ value: Double) -> String { String(format: "$%.2f", value) }
func decimal(_ value: Double) -> String { String(format: "%.1f", value).replacingOccurrences(of: ".0", with: "") }

enum Dates {
    static let day: DateFormatter = { let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy-MM-dd"; return f }()
    static let reset: DateFormatter = { let f = DateFormatter(); f.dateFormat = "d MMM · h:mm a"; return f }()
    static let clock: DateFormatter = { let f = DateFormatter(); f.dateFormat = "h:mm a"; return f }()
    static let short: DateFormatter = { let f = DateFormatter(); f.dateFormat = "d MMM"; return f }()
    static let long: DateFormatter = { let f = DateFormatter(); f.dateFormat = "d MMM yyyy"; return f }()
    static func range(_ preset: Int, now: Date = Date()) -> (String, String) {
        let c = Calendar.current
        let start: Date
        switch preset {
        case 0: start = c.startOfDay(for: now)
        case 1: start = c.dateInterval(of: .weekOfYear, for: now)!.start
        case 2: start = c.dateInterval(of: .month, for: now)!.start
        case 3: start = c.dateInterval(of: .year, for: now)!.start
        default: return ("0000-01-01", day.string(from: now))
        }
        return (day.string(from: start), day.string(from: now))
    }
}

/// The guard has its own freshness deadline; a hung collector cannot disable it.
struct NativeGuard {
    var armed = false
    var blocked = false
    var afterReset = false
    var currentLimit: Double = 95
    var resetLimit: Double = 5
    var previous: QuotaReading?
    var reason = ""
    var limit: Double { afterReset ? resetLimit : currentLimit }

    mutating func arm(_ reading: QuotaReading?, now: Double) throws {
        guard currentLimit.isFinite, resetLimit.isFinite,
              (0...100).contains(currentLimit), (0...100).contains(resetLimit) else {
            throw NativeError.message("Enter limits from 0 to 100%.")
        }
        guard let reading, reading.isFresh(at: now) else {
            throw NativeError.message("Refresh quota before arming the cutoff.")
        }
        armed = true; blocked = false; afterReset = false; previous = nil; reason = ""
        observe(reading, now: now)
    }
    mutating func observe(_ reading: QuotaReading, now: Double) {
        guard armed, !blocked else { return }
        guard reading.isFresh(at: now) else { checkAge(now: now); return }
        if let old = previous {
            guard reading.timestamp > old.timestamp else { checkAge(now: now); return }
            if reading.weekly!.id != old.weekly!.id || reading.weekly!.percent! < old.weekly!.percent! ||
                abs(reading.weekly!.reset! - old.weekly!.reset!) > 60 || now >= old.weekly!.reset! {
                afterReset = true
            }
        }
        previous = reading
        if reading.weekly!.percent! >= limit {
            blocked = true
            reason = String(format: "%.0f%% reached (%.0f%% limit).", reading.weekly!.percent!, limit)
        }
    }
    mutating func checkAge(now: Double) {
        guard armed, !blocked, let previous else { return }
        if now - previous.timestamp > 120 || now < previous.timestamp - 5 {
            blocked = true; reason = "Quota is stale. Stop requested."
        }
    }
    mutating func disarm() { armed = false; blocked = false; reason = ""; previous = nil }
}

enum NativeError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}

func jsonDecode<T: Decodable>(_ type: T.Type, _ value: Any) throws -> T {
    try JSONDecoder().decode(type, from: JSONSerialization.data(withJSONObject: value))
}
