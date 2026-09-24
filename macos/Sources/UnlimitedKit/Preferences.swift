import Foundation

/// The owner's choices per account, keyed by tile id: its label, whether it shows, and where.
public struct Preferences: Codable, Equatable, Sendable {
    public var labels: [String: String] = [:]
    public var hidden: Set<String> = []
    public var order: [String] = []

    public init() {}

    /// The strip as the owner arranged it. An account seen for the first time keeps the label it
    /// arrives with from then on, so a newly discovered identity never renames an existing tile.
    public mutating func apply(_ tiles: [Tile]) -> [Tile] {
        for t in tiles where labels[t.id] == nil && !t.id.isEmpty {
            labels[t.id] = t.label
            order.append(t.id)
        }
        let rank = Dictionary(order.enumerated().map { ($1, $0) }, uniquingKeysWith: { a, _ in a })
        return tiles
            .filter { !hidden.contains($0.id) }
            .sorted { (rank[$0.id] ?? .max) < (rank[$1.id] ?? .max) }
            .map { $0.labelled(labels[$0.id] ?? $0.label) }
    }

    public mutating func rename(_ id: String, to label: String) {
        labels[id] = String(label.uppercased().prefix(4))
    }

    public mutating func hide(_ id: String, _ hide: Bool) {
        if hide { hidden.insert(id) } else { hidden.remove(id) }
    }

    /// One place up (-1) or down (+1) among the accounts that show, past any hidden between.
    public mutating func step(_ id: String, by step: Int) {
        let shown = order.filter { !hidden.contains($0) }
        guard let i = shown.firstIndex(of: id), shown.indices.contains(i + step),
              let target = order.firstIndex(of: shown[i + step]) else { return }
        move(id, to: target)
    }

    public mutating func move(_ id: String, to index: Int) {
        guard let from = order.firstIndex(of: id) else { return }
        order.remove(at: from)
        order.insert(id, at: min(max(index, 0), order.count))
    }
}
