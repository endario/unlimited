import Foundation

/// A traffic light for one window: whether to use it more, carry on, or back off. The thresholds
/// are the app's; `unlimited` reports facts and draws none.
public enum Health: Int, Comparable, Sendable {
    case useMore, normal, amber, red

    public static func < (a: Health, b: Health) -> Bool { a.rawValue < b.rawValue }

    /// Tunable: a window heading to reset below this has room to spare.
    public static let roomBelow = 0.7
    /// Tunable: before this much of its window, and with fewer past windows than
    /// `trustPastWindows`, a projection is not drawn.
    public static let trustAfter = 0.1
    public static let trustPastWindows = 3
}

public struct Projection: Decodable, Sendable {
    public let atReset: [Double]
    public let exhaustsAt: Date?
    public let pastWindows: Int?
    /// How likely use passes the limit, from past windows that did; nil without them.
    public var runOut: Double? = nil

    public init(atReset: [Double], exhaustsAt: Date?, pastWindows: Int?, runOut: Double? = nil) {
        (self.atReset, self.exhaustsAt, self.pastWindows, self.runOut) = (atReset, exhaustsAt, pastWindows, runOut)
    }
}

extension Limit {
    public func health(now: Date) -> Health {
        if held == true || (usedAtLeast ?? 0) >= 1 { return .red }
        guard let p = projection, p.atReset.count == 2, trusted(p, now: now) else { return .normal }
        let (lo, hi) = (p.atReset[0], p.atReset[1])
        if lo >= 1 { return .red }
        if hi >= 1 { return .amber }
        return hi < Health.roomBelow ? .useMore : .normal
    }

    func trusted(_ p: Projection, now: Date) -> Bool {
        if (p.pastWindows ?? 0) >= Health.trustPastWindows { return true }
        guard let minutes = windowMinutes, let resets = resetsAt else { return false }
        let length = Double(minutes) * 60
        return (length - resets.timeIntervalSince(now)) / length >= Health.trustAfter
    }
}
