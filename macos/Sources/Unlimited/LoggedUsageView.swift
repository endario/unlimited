import SwiftUI
import UnlimitedKit

struct LoggedUsageDetail {
    struct Period: Identifiable {
        let windowMinutes: Int
        let title: String
        let range: String
        let total: String
        let coverage: String

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
                   coverage: Self.coverage(period))
        }
    }

    private static func timestamp(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter.string(from: date)
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

    var body: some View {
        let detail = LoggedUsageDetail(usage: usage, now: now)
        Box {
            VStack(alignment: .leading, spacing: 6) {
                Text("Logged tokens").font(.callout.weight(.semibold))
                Text(detail.source).font(.caption).foregroundStyle(.secondary)
                Text(detail.snapshot).font(.caption).foregroundStyle(.secondary)
                if let diagnostic = detail.diagnostic {
                    Text(diagnostic).font(.caption).foregroundStyle(.secondary)
                }
                ForEach(detail.periods) { period in
                    VStack(alignment: .leading, spacing: 1) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(period.title)
                            Spacer()
                            Text(period.total).monospacedDigit()
                        }
                        .font(.callout)
                        Text(period.range).font(.caption).foregroundStyle(.secondary)
                        Text(period.coverage).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
        }
        .help("Locally observed tokens from the gateway log. These are not a quota or availability forecast.")
    }
}
