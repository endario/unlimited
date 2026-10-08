import Testing
@testable import Unlimited

@Test func hoverExitKeepsExpansionForFiveSecondsWithoutExtendingOnOutsideMovement() {
    var delay = HoverDelay()
    let now = ContinuousClock.now
    delay.update(inside: true, open: false, now: now)
    #expect(delay.expanded)
    delay.update(inside: false, open: false, now: now)
    #expect(delay.collapseAt == now.advanced(by: .seconds(5)))
    delay.update(inside: false, open: false, now: now.advanced(by: .seconds(3)))
    #expect(delay.collapseAt == now.advanced(by: .seconds(5)))
    let early = delay.collapse(now: now.advanced(by: .seconds(4.999)))
    #expect(!early)
    #expect(delay.expanded)
    let due = delay.collapse(now: now.advanced(by: .seconds(5)))
    #expect(due)
    #expect(!delay.expanded)
    #expect(delay.collapseAt == nil)
}

@Test func reentryCancelsThePendingCollapse() {
    var delay = HoverDelay()
    let now = ContinuousClock.now
    delay.update(inside: true, open: false, now: now)
    delay.update(inside: false, open: false, now: now)
    delay.update(inside: true, open: false, now: now.advanced(by: .seconds(4)))
    #expect(delay.collapseAt == nil)
    let collapsed = delay.collapse(now: now.advanced(by: .seconds(10)))
    #expect(!collapsed)
    #expect(delay.expanded)
}

@Test func openPopoverPinsExpansionAndClosingStartsANewGracePeriod() {
    var delay = HoverDelay()
    let now = ContinuousClock.now
    delay.update(inside: true, open: false, now: now)
    delay.update(inside: false, open: false, now: now)
    delay.update(inside: false, open: true, now: now.advanced(by: .seconds(4)))
    #expect(delay.collapseAt == nil)
    let collapsed = delay.collapse(now: now.advanced(by: .seconds(20)))
    #expect(!collapsed)
    delay.update(inside: false, open: false, now: now.advanced(by: .seconds(20)))
    #expect(delay.collapseAt == now.advanced(by: .seconds(25)))
    delay.reset()
    #expect(!delay.expanded)
    #expect(delay.collapseAt == nil)
}

@Test func outsideMovementWithoutPriorHoverDoesNotExpandOrScheduleCollapse() {
    var delay = HoverDelay()
    delay.update(inside: false, open: false, now: .now)
    #expect(!delay.expanded)
    #expect(delay.collapseAt == nil)
}
