struct HoverDelay {
    private(set) var expanded = false
    private(set) var collapseAt: ContinuousClock.Instant?
    static let duration: Duration = .seconds(5)

    mutating func update(inside: Bool, open: Bool, now: ContinuousClock.Instant) {
        if inside || open {
            expanded = true
            collapseAt = nil
        } else if expanded && collapseAt == nil {
            collapseAt = now.advanced(by: Self.duration)
        }
    }

    mutating func collapse(now: ContinuousClock.Instant) -> Bool {
        guard let collapseAt, now >= collapseAt else { return false }
        reset()
        return true
    }

    mutating func reset() {
        expanded = false
        collapseAt = nil
    }
}
