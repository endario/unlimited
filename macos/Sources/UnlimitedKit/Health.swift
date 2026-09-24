import Foundation

/// A traffic light for one window. Blue: its room expires soon, spend it. Green: under-used.
/// Neutral: carry on. Amber and red: back off. The thresholds are the app's; `unlimited` reports
/// facts and draws none.
public enum Health: Int, Comparable, Sendable {
    case sprint, underUsed, normal, amber, red

    public static func < (a: Health, b: Health) -> Bool { a.rawValue < b.rawValue }

    /// Tunable: a window heading to reset below this is under-used.
    public static let underUsedBelow = 0.8
    /// Tunable: in this last part of its window, room the recent pace will not spend is a sprint.
    public static let finalStretch = 0.15
    public static let sprintBelow = 0.9
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
        if hi < Health.sprintBelow, let e = elapsed(now: now), e >= 1 - Health.finalStretch { return .sprint }
        return hi < Health.underUsedBelow ? .underUsed : .normal
    }

    /// How far through its window we are, 0...1.
    public func elapsed(now: Date) -> Double? {
        guard let minutes = windowMinutes, minutes > 0, let resets = resetsAt else { return nil }
        let length = Double(minutes) * 60
        return min(max(1 - resets.timeIntervalSince(now) / length, 0), 1)
    }

    func trusted(_ p: Projection, now: Date) -> Bool {
        if (p.pastWindows ?? 0) >= Health.trustPastWindows { return true }
        return (elapsed(now: now) ?? 0) >= Health.trustAfter
    }
}
