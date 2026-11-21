import Foundation

/// The vendor's time-of-day rates from the `steering` stamp on `unlimited read --json`. It is
/// optional in schema 1 so readings from any CLI release decode.
public struct Steering: Decodable, Sendable, Equatable {
    /// The vendor's time-of-day rates, as absolute instants from now to the CLI's horizon.
    public let rates: [Rate]

    public init(rates: [Rate] = []) {
        self.rates = rates
    }

    enum CodingKeys: String, CodingKey { case rates }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        rates = try c.decodeIfPresent([Rate].self, forKey: .rates) ?? []
    }

    /// A window charged at `factor` times the usual rate; `offerings` nil covers the whole account.
    public struct Rate: Decodable, Sendable, Equatable {
        public let offerings: [String]?
        public let from: Date
        public let to: Date
        public let factor: Double
    }
}

/// Shared horizontal geometry for rendering, status-item clicks, and popover anchors.
public struct StripLayout: Sendable {
    public struct Rect: Equatable, Sendable {
        public let x: Double
        public let width: Double
        public init(x: Double, width: Double) {
            self.x = x
            self.width = width
        }
        public var minX: Double { x }
        public var maxX: Double { x + width }
    }

    public static let neutralWidth = 21.0
    public let tiles: [Tile]
    public let spacing: Double
    public let padding: Double

    public init(tiles: [Tile], spacing: Double, padding: Double) {
        self.tiles = tiles
        self.spacing = spacing
        self.padding = padding
    }

    public func width(of tile: Tile) -> Double {
        // Measured against the strip's 9pt semibold rounded value font.
        let textWidth = Double(tile.value.text.count) * 6.0
        return max(Self.neutralWidth, textWidth)
    }

    public func rect(of tile: Tile) -> Rect {
        guard let index = tiles.firstIndex(where: { $0.id == tile.id }) else { return Rect(x: padding, width: width(of: tile)) }
        let x = tiles[..<index].reduce(padding) { $0 + width(of: $1) + spacing }
        return Rect(x: x, width: width(of: tile))
    }

    public func tile(at x: Double) -> Tile? {
        guard let first = tiles.first, let last = tiles.last else { return nil }
        if x < padding { return first }
        for tile in tiles {
            let rect = rect(of: tile)
            if x < rect.maxX { return tile }
            if x < rect.maxX + spacing { return tile }
        }
        return last
    }
}
