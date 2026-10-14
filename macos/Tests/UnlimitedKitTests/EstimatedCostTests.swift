import Foundation
import Testing
@testable import UnlimitedKit

@Test func weeklyMarketCostUsesTheLocalBudgetWithoutEnteringQuotaSelection() throws {
    let readings = try Reading.decode(Data("""
    [{"schema": 1, "vendor": "woyo", "account": "estimated", "status": "ok",
      "taken_at": "2026-09-24T05:59:00+00:00", "limits": [],
      "usage": {"source": "gateway-log", "as_of": "2026-09-24T06:00:00+00:00",
        "periods": [{"window_minutes": 10080, "starts_at": "2026-09-17T06:00:00+00:00",
          "ends_at": "2026-09-24T06:00:00+00:00", "total_tokens": 1000000,
          "coverage": "partial", "reasons": ["horizon-edge"],
          "estimated_cost": {"usd": 50, "budget_usd": 200, "unpriced_requests": 2, "status": "partial"}}]}}]
    """.utf8))
    let period = try #require(readings[0].usage?.period(windowMinutes: 10080))
    let tile = try #require(Tile.strip(readings, now: now).first)
    #expect(tile.value == .localPercent(25))
    let card = try #require(Card.localWeekly(period))
    #expect(card.used == 0.25)
    #expect(card.title == "Rolling weekly")
    #expect(period.estimatedCost?.unpricedRequests == 2)
    let quota = Tile(id: "woyo/quota", label: "WY2", value: .percent(60), dimmed: false)
    #expect(Tile.pick([tile, quota]) == quota.id)
}

@Test func invalidCostBudgetCannotBecomeAPercentage() throws {
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    for cost in [
        #"{"usd":50,"budget_usd":0,"unpriced_requests":0,"status":"ok"}"#,
        #"{"usd":50,"budget_usd":200,"unpriced_requests":0,"status":"invalid"}"#,
        #"{"usd":null,"budget_usd":200,"unpriced_requests":1,"status":"partial"}"#
    ] {
        #expect(try decoder.decode(EstimatedCost.self, from: Data(cost.utf8)).fraction == nil)
    }
}
