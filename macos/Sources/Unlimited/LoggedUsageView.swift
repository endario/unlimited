import SwiftUI
import UnlimitedKit

struct LoggedUsageDetail {
    struct Period: Identifiable {
        let windowMinutes: Int
        let title: String
        let range: String
        let total: String
        let coverage: String
        let localPacing: String?

        var id: Int { windowMinutes }
    }

    let source: String
    let snapshot: String
    let diagnostic: String?
    let periods: [Period]

    init(usage: MeasuredUsage, now: Date) {
        diagnostic = usage.diagnostic.map { _ in
            "Local logged-usage state cannot be read. Totals are unavailable until it is repaired."
        }
        snapshot = "Aggregate snapshot: \(Self.timestamp(usage.asOf)) UTC"
        if let collectedAt = usage.collectedAt {
            source = "Observed from \(usage.source.replacing("-", with: " ")) · collected \(Card.until(now, collectedAt)) ago"
        } else {
            source = "Observed from \(usage.source.replacing("-", with: " ")) · collection time unavailable"
        }
        periods = usage.periods.map { period in
            Period(windowMinutes: period.windowMinutes, title: "Rolling \(period.windowMinutes / 1_440)d",
                   range: "\(Self.timestamp(period.startsAt))–\(Self.timestamp(period.endsAt)) UTC",
                   total: period.totalTokens.map { Tile.Value.formatTokens($0) + " tokens" } ?? "Unknown tokens",
                   coverage: Self.coverage(period), localPacing: Self.localPacing(period))
        }
    }

    private static func timestamp(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter.string(from: date)
    }

    private static func localPacing(_ period: MeasuredPeriod) -> String? {
        guard let budget = period.localBudget else { return nil }
        let profiles = budget.profiles.isEmpty ? "" : " · profiles " + budget.profiles.joined(separator: ", ")
        switch budget.status {
        case "invalid": return "Local pacing: configuration invalid" + profiles
        case "conflict": return "Local pacing: configuration conflict" + profiles
        case "ok":
            guard let limit = budget.tokenLimit, limit > 0 else {
                return "Local pacing: configuration invalid" + profiles
            }
            guard let total = period.totalTokens else {
                return "Local pacing: \(Tile.Value.formatTokens(limit))-token limit · total unknown" + history(period) + profiles
            }
            let percent = Double(total) / Double(limit) * 100
            return "Local pacing: \(Tile.Value.formatTokens(total)) / \(Tile.Value.formatTokens(limit)) tokens (\(String(format: "%.1f", percent))%)" + history(period) + profiles
        default: return "Local pacing: configuration invalid" + profiles
        }
    }

    private static func history(_ period: MeasuredPeriod) -> String {
        switch period.coverage {
        case "observed": return ""
        case "partial": return " · partial history"
        default: return " · history unknown"
        }
    }

    private static func coverage(_ period: MeasuredPeriod) -> String {
        switch period.coverage {
        case "observed": return "Observed coverage"
        case "partial":
            let explanations = period.reasons.map { reason in
                switch reason {
                case "horizon-edge": "history begins at the observation horizon"
                case "tail-gap": "a collection gap intersects this period"
                case "unparsed": "some log entries could not be parsed"
                default: reason
                }
            }
            return "Partial coverage" + (explanations.isEmpty ? "" : " · " + explanations.joined(separator: "; "))
        default: return "Coverage unknown"
        }
    }
}

struct LoggedUsageView: View {
    let usage: MeasuredUsage
    var now = Date()

    private func costDetail(_ period: MeasuredPeriod) -> String {
        guard let cost = period.estimatedCost else { return "Locally declared weekly limit" }
        if cost.status == "invalid" || cost.status == "conflict" {
            return "Estimated cost configuration: \(cost.status)"
        }
        guard let usd = cost.usd, let budget = cost.budgetUsd else {
            return "Estimated cost unavailable · missing model or pricing data"
        }
        let amount = String(format: "Estimated $%.2f / $%.2f USD", usd, budget)
        let partial = cost.status == "partial" || period.coverage != "observed"
        return amount + (partial ? " · partial history" : "")
            + (cost.unpricedRequests > 0 ? " · \(cost.unpricedRequests) unpriced requests" : "")
    }

    var body: some View {
        if let weekly = usage.period(windowMinutes: 10_080) {
            VStack(alignment: .leading, spacing: 8) {
                if let card = Card.localWeekly(weekly) {
                    CardView(card: card, showsPace: false)
                        .help(costDetail(weekly))
                }
                Box {
                    HStack(alignment: .firstTextBaseline) {
                        Text("Total tokens · 7d")
                        Spacer()
                        Text(weekly.totalTokens.map(Tile.Value.formatTokens) ?? "?")
                            .monospacedDigit()
                    }
                    .font(.callout)
                }
                .help(costDetail(weekly))
            }
            .help(LoggedUsageDetail(usage: usage, now: now).snapshot + " · " + weekly.coverage + " history")
        }
    }
}
