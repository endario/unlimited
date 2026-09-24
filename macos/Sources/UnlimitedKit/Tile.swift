import Foundation

/// One account in the menu bar: its label and what its weekly window says.
public struct Tile: Identifiable, Equatable, Sendable {
    public enum Value: Equatable, Sendable {
        case percent(Int)
        case unread      // the vendor refused, or could not be read
        case stale       // the last reading is older than `staleAfter`
        case noWeekly    // the vendor reports no weekly window for this account
        case unknown     // a weekly window whose figure is not known (the moment after a reset)
        case waiting     // nothing read yet

        public var text: String {
            switch self {
            case .percent(let p): "\(p)"
            case .unread, .stale: "!"
            case .noWeekly: "—"
            case .unknown: "?"
            case .waiting: "…"
            }
        }
    }

    /// Another window more likely to stop this account than its weekly one.
    public struct Alternate: Equatable, Sendable {
        public let role: String
        public let value: Value
        public let health: Health
    }

    public let id: String
    public let label: String
    public let value: Value
    /// A throttled or unusable reading: shown, but not to be trusted as current.
    public let dimmed: Bool
    public var health: Health = .normal
    public var alternate: Alternate?

    public init(id: String, label: String, value: Value, dimmed: Bool, health: Health = .normal,
                alternate: Alternate? = nil) {
        (self.id, self.label, self.value, self.dimmed, self.health, self.alternate) = (id, label, value, dimmed, health, alternate)
    }

    public static let waiting = Tile(id: "", label: "", value: .waiting, dimmed: false)
    /// The whole strip when `unlimited` cannot be run; the menu says why.
    public static let broken = Tile(id: "", label: "", value: .unread, dimmed: false)

    /// The non-weekly window in a worse state than the weekly one; of equals, the one that runs
    /// out first.
    static func override(_ limits: [Limit], weekly: Limit?, now: Date) -> Limit? {
        let floor = weekly?.health(now: now) ?? .normal
        return limits
            .filter { $0.role != nil && $0.role != "weekly" && $0.health(now: now) > floor }
            .min { a, b in
                let (ha, hb) = (a.health(now: now), b.health(now: now))
                if ha != hb { return ha > hb }
                return (a.projection?.exhaustsAt ?? .distantFuture) < (b.projection?.exhaustsAt ?? .distantFuture)
            }
    }
    /// Tunable: a reading older than this says nothing about now.
    public static let staleAfter: TimeInterval = 15 * 60

    static let vendors: [(id: String, code: String)] = [
        ("anthropic", "CL"), ("openai", "CDX"), ("zai", "ZAI"), ("kimi", "KMI"), ("opencode", "OPC"), ("xai", "GRK"),
    ]

    /// Tiles in vendor order, then by label; one waiting tile when there is nothing to show.
    public static func strip(_ readings: [Reading], now: Date) -> [Tile] {
        let order = Dictionary(uniqueKeysWithValues: vendors.enumerated().map { ($1.id, $0) })
        let tiles = readings
            .map { tile($0, now: now) }
            .sorted { (order[$0.vendor] ?? .max, $0.tile.label, $0.tile.id) < (order[$1.vendor] ?? .max, $1.tile.label, $1.tile.id) }
            .map(\.tile)
        // Accounts without identity names (Codex, Grok) share a label; number them apart.
        let counts = Dictionary(tiles.map { ($0.label, 1) }, uniquingKeysWith: +)
        var seen: [String: Int] = [:]
        let numbered = tiles.map { t -> Tile in
            guard counts[t.label, default: 0] > 1 else { return t }
            seen[t.label, default: 0] += 1
            return Tile(id: t.id, label: t.label + String(seen[t.label]!), value: t.value, dimmed: t.dimmed,
                        health: t.health, alternate: t.alternate)
        }
        return numbered.isEmpty ? [.waiting] : numbered
    }

    static func tile(_ r: Reading, now: Date) -> (vendor: String, tile: Tile) {
        let throttled = r.retryUntil.map { $0 > now } ?? false
        let stale = r.takenAt.map { now.timeIntervalSince($0) > staleAfter } ?? true
        let value: Value
        if r.status != "ok" {
            value = .unread
        } else if stale && !throttled {
            value = .stale
        } else if let w = r.weekly {
            value = w.usedAtLeast.map { .percent(Int(($0 * 100).rounded())) } ?? .unknown
        } else {
            value = .noWeekly
        }
        let id = "\(r.vendor)/\(r.account ?? "")"
        let usable = r.status == "ok" && !(stale && !throttled)
        let alt = usable ? override(r.limits, weekly: r.weekly, now: now).map {
            Alternate(role: $0.role ?? "", value: $0.usedAtLeast.map { .percent(Int(($0 * 100).rounded())) } ?? .unknown,
                      health: $0.health(now: now))
        } : nil
        return (r.vendor, Tile(id: id, label: label(r), value: value, dimmed: throttled || !usable,
                               health: usable ? r.weekly?.health(now: now) ?? .normal : .normal, alternate: alt))
    }

    /// The vendor's code plus the number in its first identity name: `account2` → CL2,
    /// `claude-glm-2` → ZAI2, `opencode` → OPC. Claude's first directory is `account1`, so CL1.
    static func label(_ r: Reading) -> String {
        let code = vendors.first { $0.id == r.vendor }?.code ?? String(r.vendor.prefix(3)).uppercased()
        let digits = r.names.first.map { String($0.reversed().prefix { $0.isNumber }.reversed()) } ?? ""
        return code + digits
    }
}
