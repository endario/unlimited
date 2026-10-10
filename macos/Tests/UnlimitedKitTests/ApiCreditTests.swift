import Foundation
import Testing
@testable import UnlimitedKit

private func readings(_ rows: [[String: Any]]) throws -> [Reading] {
    let body = rows.map { ["schema": 1, "status": "ok", "limits": [], "taken_at": "2026-09-24T05:53:26+00:00"].merging($0) { $1 } }
    return try Reading.decode(JSONSerialization.data(withJSONObject: body))
}

private var console: [String: Any] { [
    "vendor": "anthropic-console", "account": "chrome:Default", "vendor_account": "login-1",
    "credits": ["enabled": true, "balance": 199.5, "currency": "USD", "expires_at": "2026-11-04T00:00:00+00:00"]] }

@Test func theConsoleCreditRidesOnTheClaudeAccountOfTheSameLogin() throws {
    let rs = try readings([["vendor": "anthropic", "account": "login-1", "vendor_account": "login-1"],
                           ["vendor": "anthropic", "account": "login-2", "vendor_account": "login-2"], console])
    #expect(ApiCredit.linked(to: rs[0], in: rs)?.vendor == "anthropic-console")
    #expect(ApiCredit.linked(to: rs[1], in: rs) == nil)
    #expect(ApiCredit.folded(rs).map(\.vendor) == ["anthropic", "anthropic"])
}

@Test func aConsoleCreditWithNoClaudeAccountKeepsItsOwnTile() throws {
    let rs = try readings([["vendor": "anthropic", "account": "login-2", "vendor_account": "login-2"], console])
    #expect(ApiCredit.folded(rs).map(\.vendor) == ["anthropic", "anthropic-console"])
}

@Test func theLineSaysWhatIsLeftAndWhenItLapses() throws {
    let r = try #require(try readings([console]).first)
    let at = ISO8601DateFormatter().date(from: "2026-10-10T12:00:00Z")!
    #expect(ApiCredit.line(r, now: at) == "USD 199.50 left · expires in 24d 12h")
    #expect(ApiCredit.line(r, now: at.addingTimeInterval(60 * 86400)) == "USD 199.50 left · expired")
}

@Test func aLinkedAccountRenewsInItsOwnProfileAndAnUnlinkedOneGetsANewOne() throws {
    let rs = try readings([["vendor": "anthropic", "account": "login-1", "vendor_account": "login-1", "names": ["account1"]],
                           ["vendor": "anthropic", "account": "login-2", "vendor_account": "login-2", "names": ["account2"]], console])
    #expect(ApiCredit.profile(for: rs[0], linked: ApiCredit.linked(to: rs[0], in: rs)) == "Default")
    #expect(ApiCredit.profile(for: rs[1], linked: nil) == "Unlimited account2")
}

@Test func anUnreadConsoleHasNoBalanceLine() throws {
    let r = try #require(try readings([console.merging(["status": "refused", "why": "http-403"]) { $1 }]).first)
    #expect(ApiCredit.line(r, now: now) == nil)
}
