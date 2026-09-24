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
    /// Time to the reset, and (for a tooltip) when it is.
    public let resets: String?
    public let resetsAt: String?
    /// Where today's pace alone reaches by the reset.
    public let momentum: Double?
    /// Where the forecast puts it at the reset: the middle of its range.
    public let projected: Double?
    /// Time until it runs out, or "now".
    public let runsOut: String?
    /// Chance of running out, in percent, from past windows.
    public let odds: Int?
    /// Whether the forecast rests on enough past windows to be more than this window's paces.
    public let fromHistory: Bool

    /// How far a marker past 100% may bleed beyond the bar's end, in points: the bar is inset
    /// this much at each end of its card, so the mark lands in that space.
    public static let bleed: Double = 5

    /// A marker's position on a bar `width` wide: to scale up to the limit, then only just past it.
    public static func marker(_ value: Double, width: Double) -> Double {
        value > 1 ? width + bleed : max(value, 0) * width
    }

    static let order = ["session", "weekly", "weekly_model", "month", "extra"]

    public static func cards(_ r: Reading, now: Date, timeZone: TimeZone = .current) -> [Card] {
        r.limits
            // As `unlimited`'s status view: a window with a figure, or anything the vendor holds.
            .filter { ($0.role != nil && $0.usedAtLeast != nil) || $0.held == true }
            .sorted { (order.firstIndex(of: $0.role ?? "") ?? 9) < (order.firstIndex(of: $1.role ?? "") ?? 9) }
            .map { l in
                let length = Double(l.windowMinutes ?? 0) * 60
                let left = l.resetsAt.map { $0.timeIntervalSince(now) }
                let p = l.held == true || (l.usedAtLeast ?? 0) >= 1 ? nil : l.projection
                let ends = p?.exhaustsAt.flatMap { $0 > now ? until($0, now) : "now" }
                return Card(name: l.name, title: title(l), used: l.usedAtLeast,
                            elapsed: length > 0 && left != nil ? min(max(1 - left! / length, 0), 1) : 0,
                            health: l.health(now: now),
                            resets: l.resetsAt.map { until($0, now) },
                            resetsAt: l.resetsAt.map { "Resets \(clock($0, timeZone))" },
                            momentum: p?.recentAtReset,
                            projected: p.flatMap { $0.atReset.count == 2 ? ($0.atReset[0] + $0.atReset[1]) / 2 : nil },
                            runsOut: ends, odds: p?.runOut.map { Int(($0 * 100).rounded()) },
                            fromHistory: (p?.pastWindows ?? 0) >= Health.trustPastWindows)
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
