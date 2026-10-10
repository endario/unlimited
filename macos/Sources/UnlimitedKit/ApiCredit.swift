import Foundation

/// The API's prepaid credit, read on the Claude Console sign-in (`anthropic-console`). It is shown with
/// the Claude Code account of the same login, not as a tile of its own.
public enum ApiCredit {
    public static let vendor = "anthropic-console"
    public static let console = URL(string: "https://platform.claude.com/settings/billing")!

    /// The Console reading for a Claude account's login, where this machine has one.
    public static func linked(to r: Reading, in readings: [Reading]) -> Reading? {
        guard r.vendor == "anthropic", let who = r.vendorAccount else { return nil }
        // Two profiles may hold the same login; the fresher reading speaks for it.
        return readings.filter { $0.vendor == vendor && $0.vendorAccount == who }
            .max { ($0.takenAt ?? .distantPast) < ($1.takenAt ?? .distantPast) }
    }

    /// The readings the strip shows: a Console reading linked to a Claude account rides on its tile.
    public static func folded(_ readings: [Reading]) -> [Reading] {
        let claude = Set(readings.filter { $0.vendor == "anthropic" }.compactMap(\.vendorAccount))
        return readings.filter { !($0.vendor == vendor && $0.vendorAccount.map(claude.contains) == true) }
    }

    /// The Chrome profile that holds, or will hold, this Claude account's Console sign-in. One profile
    /// keeps one Console sign-in, so an account not yet linked gets a profile of its own.
    public static func profile(for r: Reading, linked: Reading?) -> String {
        // `chrome:<profile>:<sign-in>`
        if let held = linked?.account, held.hasPrefix("chrome:"), let end = held.lastIndex(of: ":"),
           end > held.index(held.startIndex, offsetBy: 6) {
            return String(held[held.index(held.startIndex, offsetBy: 7)..<end])
        }
        return "Unlimited " + (r.names.first ?? r.account ?? "account")
    }

    /// What is left and when the earliest of it lapses; nil when the balance could not be read.
    public static func line(_ r: Reading, now: Date) -> String? {
        guard r.status == "ok", let c = r.credits, let balance = c.balance else { return nil }
        let left = "\(c.currency.map { "\($0) " } ?? "")\(String(format: "%.2f", balance)) left"
        guard let end = c.expiresAt else { return left }
        return left + (end > now ? " · expires in \(Card.until(end, now))" : " · expired")
    }
}
