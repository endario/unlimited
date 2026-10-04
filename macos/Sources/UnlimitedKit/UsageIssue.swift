import Foundation

public struct UsageIssue: Equatable, Sendable {
    public let title: String
    public let message: String
    public let offersLogin: Bool
}

private struct UsageErrorCatalog: Decodable {
    struct Entry: Decodable {
        let title: String
        let message: String
        let auth: Bool?
    }

    let exact: [String: Entry]
    let prefixes: [String: Entry]
    let fallback: Entry

    static let bundled: UsageErrorCatalog = {
        // Native SwiftPM's accessor does not search an installed app's Contents/Resources.
        let packaged = (Bundle.main.resourceURL?.appending(path: "Unlimited_UnlimitedKit.bundle"))
            .flatMap { Bundle(url: $0) }
        let url = (packaged ?? Bundle.module).url(forResource: "usage_errors", withExtension: "json")!
        return try! JSONDecoder().decode(UsageErrorCatalog.self, from: Data(contentsOf: url))
    }()

    func describe(_ why: String?) -> Entry {
        let prefix = prefixes.keys.sorted { $0.count > $1.count }.first { why?.hasPrefix($0) == true }
        let entry = why.flatMap { exact[$0] } ?? prefix.flatMap { prefixes[$0] } ?? fallback
        return Entry(title: entry.title, message: entry.message.replacingOccurrences(of: "{reason}", with: why ?? "not reported"),
                     auth: entry.auth)
    }
}

extension Reading {
    public func issue(now: Date) -> UsageIssue? {
        let retry = retryUntil.flatMap { $0 > now ? " Retry in \(Card.until($0, now))." : nil } ?? ""
        if status == "ok" {
            if !retry.isEmpty {
                return UsageIssue(title: "Refresh delayed",
                                  message: "Usage refresh is paused. Any figures shown are the last recorded usage." + retry,
                                  offersLogin: false)
            }
            if takenAt.map({ now.timeIntervalSince($0) > Tile.staleAfter }) ?? true {
                return UsageIssue(title: "Usage is out of date",
                                  message: "No fresh usage reading is available. Refresh to try again.", offersLogin: false)
            }
            return nil
        }
        let description = UsageErrorCatalog.bundled.describe(why)
        return UsageIssue(title: description.title, message: description.message + retry,
                          offersLogin: description.auth == true && vendor == "anthropic")
    }
}
