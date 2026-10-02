import Foundation

/// Small command/read ordering seam: a later command prevents an earlier read from publishing.
public struct SteeringPublicationFence: Sendable {
    private var sequence = 0
    public private(set) var pending = false

    public init() {}

    public mutating func beginRead() -> Int? {
        guard !pending else { return nil }
        sequence += 1
        return sequence
    }

    public mutating func beginCommand() -> Int? {
        guard !pending else { return nil }
        pending = true
        sequence += 1
        return sequence
    }

    public func accepts(_ token: Int) -> Bool { token == sequence }

    @discardableResult public mutating func finish(_ token: Int) -> Bool {
        guard token == sequence else { return false }
        pending = false
        return true
    }
}

/// The live policy overlay from `unlimited read --json`. It is optional in schema 1 so clients
/// built before steering continue to decode the same readings.
public struct Steering: Decodable, Sendable, Equatable {
    public let settings: [Setting]
    public let routes: [Route]

    public init(settings: [Setting] = [], routes: [Route] = []) {
        self.settings = settings
        self.routes = routes
    }

    enum CodingKeys: String, CodingKey { case settings, routes }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        settings = try c.decodeIfPresent([Setting].self, forKey: .settings) ?? []
        routes = try c.decodeIfPresent([Route].self, forKey: .routes) ?? []
    }

    public struct Setting: Decodable, Sendable, Equatable, Identifiable {
        public let target: String
        public let account: String?
        public let multiplier: Double
        public let until: Date
        public var id: String { "\(target)|\(account ?? "")|\(until.timeIntervalSince1970)" }
    }

    public struct Route: Decodable, Sendable, Equatable, Identifiable {
        public let id: String
        public let target: String
        public let account: String?
        public let multiplier: Double
        public let until: Date

        public init(id: String, target: String, account: String? = nil, multiplier: Double, until: Date) {
            self.id = id
            self.target = target
            self.account = account
            self.multiplier = multiplier
            self.until = until
        }
    }
}

/// The strongest live route winners in each direction. Both marks are retained for mixed routes;
/// the app never collapses opposing policies into an invented account-wide multiplier.
public struct SteeringIndicator: Equatable, Sendable {
    public struct Mark: Equatable, Sendable {
        public let multiplier: Double
        public let triangles: Int
    }

    public let up: Mark?
    public let down: Mark?
    public let tooltip: String

    public init(routes: [Steering.Route], now: Date) {
        let live = routes.filter { $0.until > now && $0.multiplier.isFinite && $0.multiplier > 0 && $0.multiplier != 1 }
        let ups = live.filter { $0.multiplier > 1 }
        let downs = live.filter { $0.multiplier < 1 }
        let upRoute = ups.max { $0.multiplier < $1.multiplier }
        let downRoute = downs.min { $0.multiplier < $1.multiplier }
        up = upRoute.map { Mark(multiplier: $0.multiplier, triangles: Self.triangles(for: $0.multiplier)) }
        down = downRoute.map { Mark(multiplier: $0.multiplier, triangles: Self.triangles(for: $0.multiplier)) }
        tooltip = live.map(Self.describe).joined(separator: "\n")
    }

    /// Number of compact direction glyphs, never the number of arrowheads inside one glyph.
    public var count: Int { (up == nil ? 0 : 1) + (down == nil ? 0 : 1) }

    public static func triangles(for multiplier: Double) -> Int {
        guard multiplier.isFinite, multiplier > 0, multiplier != 1 else { return 0 }
        let strength = max(multiplier, 1 / multiplier)
        if strength < 5 { return 1 }
        if strength < 10 { return 2 }
        return 3
    }

    private static func describe(_ route: Steering.Route) -> String {
        "Route \(route.id) · \(route.target) · \(route.account ?? "all accounts") · \(format(route.multiplier)) · until \(ISO8601DateFormatter().string(from: route.until))"
    }

    /// Swift's spelling is the shortest text which parses back to this exact `Double`; unlike an
    /// integer conversion it is safe for every finite factor the CLI accepts.
    public static func format(_ multiplier: Double) -> String {
        let text = String(multiplier)
        return (text.hasSuffix(".0") ? String(text.dropLast(2)) : text) + "x"
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

    public static let neutralWidth = 26.0
    private static let markWidth = 7.0
    public let tiles: [Tile]
    public let spacing: Double
    public let padding: Double

    public init(tiles: [Tile], spacing: Double, padding: Double) {
        self.tiles = tiles
        self.spacing = spacing
        self.padding = padding
    }

    public func width(of tile: Tile) -> Double {
        Self.neutralWidth + Double(tile.indicator?.count ?? 0) * Self.markWidth
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
