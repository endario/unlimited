import Foundation

/// One account's reading, as `unlimited read --json` prints it (schema 1).
public struct Reading: Decodable, Sendable {
    public let vendor: String
    public let account: String?
    public let takenAt: Date?
    public let status: String
    public let why: String?
    public let retryUntil: Date?
    public let limits: [Limit]
    /// Absent before unlimited 0.0.21.
    public let names: [String]

    public static func decode(_ data: Data) throws -> [Reading] {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .custom { dec in
            let s = try dec.singleValueContainer().decode(String.self)
            guard let t = parseISO(s) else {
                throw DecodingError.dataCorrupted(.init(codingPath: dec.codingPath, debugDescription: s))
            }
            return t
        }
        let readings = try d.decode([Reading].self, from: data)
        if let other = readings.first(where: { $0.schema != 1 }) { throw SchemaError(schema: other.schema) }
        return readings
    }

    public struct SchemaError: Error { public let schema: Int }

    public let schema: Int

    public let plan: String?
    public let credits: Credits?

    enum CodingKeys: String, CodingKey {
        case schema, vendor, account, takenAt, status, why, retryUntil, limits, names, plan, credits
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        schema = try c.decode(Int.self, forKey: .schema)
        vendor = try c.decode(String.self, forKey: .vendor)
        account = try c.decodeIfPresent(String.self, forKey: .account)
        takenAt = try c.decodeIfPresent(Date.self, forKey: .takenAt)
        status = try c.decode(String.self, forKey: .status)
        why = try c.decodeIfPresent(String.self, forKey: .why)
        retryUntil = try c.decodeIfPresent(Date.self, forKey: .retryUntil)
        limits = try c.decodeIfPresent([Limit].self, forKey: .limits) ?? []
        names = try c.decodeIfPresent([String].self, forKey: .names) ?? []
        plan = try c.decodeIfPresent(String.self, forKey: .plan)
        credits = try c.decodeIfPresent(Credits.self, forKey: .credits)
    }

    /// The window the tile follows: a plan's monthly bucket where it enforces one, since that is the
    /// budget it runs out of, else its weekly window.
    public var primary: Limit? { limits.first { $0.role == "month" } ?? limits.first { $0.role == "weekly" } }
}

/// What an account may spend past its windows, in `currency`'s major units.
public struct Credits: Decodable, Sendable {
    public let enabled: Bool
    public let used: Double?
    public let limit: Double?
    public let currency: String?
}

public struct Limit: Decodable, Sendable {
    public let name: String
    public let windowMinutes: Int?
    public let usedAtLeast: Double?
    public let resetsAt: Date?
    public let held: Bool?
    /// session | weekly | weekly_model | month | extra, or nil for entries that are not windows.
    public let role: String?
    public let scope: String?
    public let projection: Projection?
}

/// Python's `isoformat()`: `2026-09-24T05:53:26.402753+00:00`, the fraction optional and up to six
/// digits, which `ISO8601DateFormatter` does not take.
func parseISO(_ s: String) -> Date? {
    var whole = s, fraction = 0.0
    if let dot = s.firstIndex(of: "."),
       let end = s[dot...].firstIndex(where: { $0 == "+" || $0 == "-" || $0 == "Z" }) {
        fraction = Double("0" + s[dot..<end]) ?? 0
        whole = String(s[..<dot] + s[end...])
    }
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    return f.date(from: whole).map { $0.addingTimeInterval(fraction) }
}
