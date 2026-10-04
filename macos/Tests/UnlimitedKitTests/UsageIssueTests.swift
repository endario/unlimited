import Foundation
import Testing
@testable import UnlimitedKit

private func issueReading(vendor: String = "anthropic", status: String = "unread", why: String? = nil,
                          takenAt: Date? = now, retryUntil: Date? = nil) throws -> Reading {
    let format = ISO8601DateFormatter()
    let body: [[String: Any]] = [["schema": 1, "vendor": vendor, "account": "a", "names": ["account2"],
                                "status": status, "why": why as Any? ?? NSNull(), "limits": [],
                                "taken_at": takenAt.map(format.string) as Any? ?? NSNull(),
                                "retry_until": retryUntil.map(format.string) as Any? ?? NSNull()]]
    return try #require(Reading.decode(JSONSerialization.data(withJSONObject: body)).first)
}

@Test(arguments: ["signed-out", "credential-expired", "no-credential", "http-401"])
func claudeAuthenticationFailuresOfferInteractiveLogin(_ why: String) throws {
    let issue = try #require(try issueReading(why: why).issue(now: now))
    #expect(issue.offersLogin)
    #expect(issue.message.contains("Sign in"))
}

@Test(arguments: ["openai", "xai", "zai"])
func anotherVendorsExpiredCredentialsDoNotOfferClaudeLogin(_ vendor: String) throws {
    let issue = try #require(try issueReading(vendor: vendor, why: "credential-expired").issue(now: now))
    #expect(!issue.offersLogin)
    #expect(issue.message.contains("Sign in"))
}

@Test(arguments: [
    ("signed-out", "Signed out"), ("credential-expired", "Sign-in expired"),
    ("no-credential", "No sign-in found"), ("http-401", "Sign-in rejected"),
    ("http-403", "Access denied"), ("unreachable", "Cannot reach usage service"),
    ("not-json", "Invalid usage response"), ("not-an-object", "Invalid usage response"),
    ("no-limits", "No usage limits reported"), ("no-subscription", "No subscription found")
])
func reportedReasonsHaveReadableExplanations(_ why: String, _ title: String) throws {
    let issue = try #require(try issueReading(why: why).issue(now: now))
    #expect(issue.title == title)
    #expect(!issue.message.isEmpty)
}

@Test(arguments: ["http-403", "http-429", "http-503", "unreachable", "not-json", "no-limits", "vendor-123", "new-reason"])
func nonAuthenticationFailuresDoNotOfferLogin(_ why: String) throws {
    #expect(try issueReading(why: why).issue(now: now)?.offersLogin == false)
}

@Test func throttlingExplainsWhenItWillRetry() throws {
    let issue = try #require(try issueReading(status: "refused", why: "http-429",
                                            retryUntil: now.addingTimeInterval(120)).issue(now: now))
    #expect(issue.title == "Usage service busy")
    #expect(issue.message.contains("2m"))
}

@Test func retainedBackoffExplainsCachedDataWithoutInventingAReason() throws {
    let r = try issueReading(status: "ok", why: "http-503", takenAt: now.addingTimeInterval(-3600),
                             retryUntil: now.addingTimeInterval(120))
    let issue = try #require(r.issue(now: now))
    #expect(issue.title == "Refresh delayed")
    #expect(issue.message.contains("last recorded usage"))
    #expect(issue.message.contains("2m"))
    #expect(!issue.message.contains("503"))
    #expect(!issue.offersLogin)
}

@Test func anOldReadingExplainsTheMissingFreshUsage() throws {
    let issue = try #require(try issueReading(status: "ok", takenAt: now.addingTimeInterval(-960)).issue(now: now))
    #expect(issue.title == "Usage is out of date")
    #expect(issue.message.contains("Refresh"))
    #expect(!issue.offersLogin)
}

@Test func aReadingWithoutATimestampDoesNotClaimFreshness() throws {
    #expect(try issueReading(status: "ok", takenAt: nil).issue(now: now)?.title == "Usage is out of date")
}

@Test func aRecoveredReadingRemovesTheFailurePanel() throws {
    #expect(try issueReading(status: "ok", why: "signed-out").issue(now: now) == nil)
    #expect(try issueReading(status: "ok", retryUntil: now.addingTimeInterval(-1)).issue(now: now) == nil)
}

@Test func anUnknownReasonStillExplainsTheFailedRead() throws {
    let issue = try #require(try issueReading(why: "new-reason").issue(now: now))
    #expect(issue.title == "Usage unavailable")
    #expect(issue.message.contains("new-reason"))
    #expect(issue.message.contains("Refresh"))
    #expect(!issue.offersLogin)
}

@Test func authenticationFailureStillExplainsTheCacheRetryDeadline() throws {
    let issue = try #require(try issueReading(status: "refused", why: "http-401",
                                            retryUntil: now.addingTimeInterval(120)).issue(now: now))
    #expect(issue.offersLogin)
    #expect(issue.message.contains("2m"), "even after sign-in, refresh respects this deadline")
}

@Test func aProviderRefusalUsesTheSharedMessageDefinition() throws {
    let issue = try #require(try issueReading(status: "refused", why: "vendor-refused").issue(now: now))
    #expect(issue.title == "Usage request refused")
    #expect(issue.message.contains("provider refused"))
    #expect(!issue.offersLogin)
}

@Test(arguments: ["http-500", "http-503"])
func serverFailuresExplainTemporaryUnavailability(_ why: String) throws {
    let issue = try #require(try issueReading(status: "refused", why: why).issue(now: now))
    #expect(issue.message.contains("temporarily unavailable"))
    #expect(!issue.offersLogin)
}

@Test func anotherHttpRefusalKeepsItsStatusInTheExplanation() throws {
    let issue = try #require(try issueReading(status: "refused", why: "http-418").issue(now: now))
    #expect(issue.message.contains("418"))
}
