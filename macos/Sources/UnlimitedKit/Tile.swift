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
    /// The vendor's account to use next: marked only where a vendor has more than one.
    public var best = false
    /// The high end of the weekly forecast, for choosing the best pick.
    var heading: Double?

    public init(id: String, label: String, value: Value, dimmed: Bool, health: Health = .normal,
                alternate: Alternate? = nil, best: Bool = false) {
        (self.id, self.label, self.value, self.dimmed, self.health, self.alternate, self.best) =
            (id, label, value, dimmed, health, alternate, best)
    }

    /// The same tile under another label.
    public func labelled(_ label: String) -> Tile {
        var t = Tile(id: id, label: label, value: value, dimmed: dimmed, health: health, alternate: alternate, best: best)
        t.heading = heading
        return t
    }

    /// Of one vendor's accounts that nothing is stopping, the one whose room expires soonest
    /// (blue), else the one with the most room at reset.
    static func pick(_ tiles: [Tile]) -> String? {
        let open = tiles.filter { t in
            guard case .percent = t.value, !t.dimmed, t.health < .amber else { return false }
            return (t.alternate?.health ?? .normal) < .amber
        }
        guard tiles.count > 1 else { return nil }
        return open.min { a, b in
            if (a.health == .sprint) != (b.health == .sprint) { return a.health == .sprint }
            return (a.heading ?? 1) < (b.heading ?? 1)
        }?.id
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

    static let vendors: [(id: String, code: String, name: String, tab: String)] = [
        ("anthropic", "CL", "Claude", "CLA"), ("openai", "CDX", "Codex", "CDX"), ("zai", "ZAI", "Z.ai", "ZAI"),
        ("kimi", "KMI", "Kimi", "KMI"), ("opencode", "OPC", "OpenCode", "OPC"), ("xai", "GRK", "Grok", "GRK"),
    ]

    /// The vendor's name for a person; an unknown vendor shows its id.
    public static func vendorName(_ id: String) -> String { vendors.first { $0.id == id }?.name ?? id }

    /// Three letters, for the popover's tabs.
    public static func vendorTab(_ id: String) -> String {
        vendors.first { $0.id == id }?.tab ?? String(id.prefix(3)).uppercased()
    }

    /// The tile under `x`, measured from the strip's leading edge.
    public static func at(_ x: Double, in tiles: [Tile], width: Double, spacing: Double, padding: Double) -> Tile? {
        guard !tiles.isEmpty else { return nil }
        let i = Int(((x - padding) / (width + spacing)).rounded(.down))
        return tiles[min(max(i, 0), tiles.count - 1)]
    }

    /// The vendor a tile's account belongs to.
    public var vendor: String { String(id.prefix { $0 != "/" }) }

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
            return t.labelled(t.label + String(seen[t.label]!))
        }
        let picks = Set(Dictionary(grouping: numbered, by: \.vendor).values.compactMap(pick))
        let marked = numbered.map { t -> Tile in
            var t = t
            t.best = picks.contains(t.id)
            return t
        }
        return marked.isEmpty ? [.waiting] : marked
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
        var tile = Tile(id: id, label: label(r), value: value, dimmed: throttled || !usable,
                        health: usable ? r.weekly?.health(now: now) ?? .normal : .normal, alternate: alt)
        // Where it is heading, if the forecast is trusted yet; else how much is used so far.
        tile.heading = r.weekly.flatMap { w in
            w.projection.flatMap { w.trusted($0, now: now) ? $0.atReset.last : nil } ?? w.usedAtLeast
        }
        return (r.vendor, tile)
    }

    /// The vendor's code plus the number in its first identity name: `account2` → CL2,
    /// `claude-glm-2` → ZAI2, `opencode` → OPC. Claude's first directory is `account1`, so CL1.
    static func label(_ r: Reading) -> String {
        let code = vendors.first { $0.id == r.vendor }?.code ?? String(r.vendor.prefix(3)).uppercased()
        let digits = r.names.first.map { String($0.reversed().prefix { $0.isNumber }.reversed()) } ?? ""
        return code + digits
    }
}
