import Foundation

/// One window in the popover.
public struct Card: Identifiable, Sendable {
    public var id: String { name }
    public let name: String
    public let title: String
    public let used: Double?
    /// How far through its window we are, 0...1: where the bar's pace tick sits.
    public let elapsed: Double
    public let health: Health
    public let resets: String?
    public let forecast: String?

    static let order = ["session", "weekly", "weekly_model", "month", "extra"]

    public static func cards(_ r: Reading, now: Date, timeZone: TimeZone = .current) -> [Card] {
        r.limits
            // As `unlimited`'s status view: a window with a figure, or anything the vendor holds.
            .filter { ($0.role != nil && $0.usedAtLeast != nil) || $0.held == true }
            .sorted { (order.firstIndex(of: $0.role ?? "") ?? 9) < (order.firstIndex(of: $1.role ?? "") ?? 9) }
            .map { l in
                let length = Double(l.windowMinutes ?? 0) * 60
                let left = l.resetsAt.map { $0.timeIntervalSince(now) }
                return Card(name: l.name, title: title(l), used: l.usedAtLeast,
                            elapsed: length > 0 && left != nil ? min(max(1 - left! / length, 0), 1) : 0,
                            health: l.health(now: now),
                            resets: l.resetsAt.map { "Resets in \(until($0, now)) · \(clock($0, timeZone))" },
                            forecast: forecast(l, now: now, timeZone: timeZone))
            }
    }

    static func title(_ l: Limit) -> String {
        switch l.role {
        case "session": "5-Hour"
        case "weekly": "Weekly"
        case "weekly_model": "Weekly · \(l.scope ?? l.name)"
        case "month": "Monthly"
        default: l.scope ?? l.name
        }
    }

    /// Where the window is heading, when it runs out, and what that rests on; the same facts
    /// `unlimited`'s status view prints under each window.
    static func forecast(_ l: Limit, now: Date, timeZone: TimeZone) -> String? {
        guard let p = l.projection, p.atReset.count == 2, l.held != true, (l.usedAtLeast ?? 0) < 1 else { return nil }
        let (lo, hi) = (Int((p.atReset[0] * 100).rounded()), Int((p.atReset[1] * 100).rounded()))
        var parts = [lo == hi ? "→ \(lo)% at reset" : "→ \(lo)–\(hi)% at reset"]
        if let ends = p.exhaustsAt {
            parts.append(ends > now ? "runs out \(clock(ends, timeZone)) (in \(until(ends, now)))" : "runs out now")
        }
        if let odds = p.runOut { parts.append("\(Int((odds * 100).rounded()))% chance of running out") }
        if let n = p.pastWindows, n >= Health.trustPastWindows { parts.append("from \(n) past windows") }
        return parts.joined(separator: " · ")
    }

    public static func credits(_ r: Reading) -> String? {
        guard let c = r.credits else { return nil }
        let unit = c.currency.map { "\($0) " } ?? ""
        let state = c.enabled ? "on" : c.disabledReason.map { "off: \($0)" } ?? "off"
        let money = { (v: Double) in String(format: "%.2f", v) }
        switch (c.used, c.limit) {
        case (0?, nil), (nil, nil): return state  // never switched on, or nothing to say
        case let (used?, limit?): return "\(unit)\(money(used)) of \(money(limit)) · \(state)"
        case let (used?, nil): return "\(unit)\(money(used)) used · \(state)"
        default: return state
        }
    }

    static let plans = ["default_claude_max_20x": "Max 20x", "default_claude_max_5x": "Max 5x",
                        "default_claude_ai": "Pro", "pro": "Pro", "plus": "Plus", "prolite": "Pro Lite",
                        "max": "Max", "lite": "Lite"]

    public static func plan(_ r: Reading) -> String? { r.plan.map { plans[$0] ?? $0 } }

    public static func until(_ t: Date, _ now: Date) -> String {
        let s = Int(t.timeIntervalSince(now))
        guard s > 0 else { return "now" }
        guard s >= 60 else { return "under a minute" }
        let (d, h, m) = (s / 86400, s % 86400 / 3600, s % 3600 / 60)
        return d > 0 ? "\(d)d \(h)h" : h > 0 ? String(format: "%dh %02dm", h, m) : "\(m)m"
    }

    static func clock(_ t: Date, _ tz: TimeZone) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = tz
        f.dateFormat = "EEE HH:mm"
        return f.string(from: t)
    }
}
