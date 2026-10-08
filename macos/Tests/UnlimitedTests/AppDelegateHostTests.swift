import AppKit
import Combine
import SwiftUI
import Testing
@testable import Unlimited
import UnlimitedKit

@MainActor
@Suite(.serialized)
struct AppDelegateHostTests {
    @Test func delayedCollapseUpdatesTheNativeHostingGeometryAfterTheFade() async throws {
        let fixture = try await DelegateHost()
        defer { fixture.close() }
        #expect(fixture.window.contentView?.bounds.width == 30)
        #expect(fixture.hostingWidth == 30)

        fixture.enter()
        #expect(fixture.window.contentView?.bounds.width == 349)
        #expect(fixture.hostingWidth == 349)
        var fadeStarted: ContinuousClock.Instant?
        var collapsed: ContinuousClock.Instant?
        var widthAtFade: CGFloat?
        let fade = fixture.model.$fadingNormal.sink { value in
            if value {
                fadeStarted = .now
                widthAtFade = fixture.hostingWidth
            }
        }
        let expansion = fixture.model.$hoverExpanded.dropFirst().sink { value in
            if !value { collapsed = .now }
        }
        defer { fade.cancel(); expansion.cancel() }
        let exited = ContinuousClock.now
        fixture.leave()
        try await waitUntil("native host did not become compact after the fade") { fixture.window.contentView?.bounds.width == 30 && !fixture.model.hoverExpanded }

        let started = try #require(fadeStarted)
        let finished = try #require(collapsed)
        #expect(exited.duration(to: started) >= .seconds(5))
        #expect(widthAtFade == 349)
        if !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            #expect(started.duration(to: finished) >= .seconds(0.7))
        }
        #expect(fixture.hostingWidth == 30)
        #expect(!fixture.model.fadingNormal)
    }

    @Test func aRealPopoverPinsExpansionAndClosingStartsAFreshGracePeriod() async throws {
        let fixture = try await DelegateHost()
        defer { fixture.close() }
        fixture.enter()
        fixture.leave()
        fixture.pointInside()
        // Avoid NSButton's tracking loop in the async test runner.
        #expect(fixture.button.sendAction(fixture.button.action, to: fixture.button.target))
        fixture.delegate.popover.contentViewController?.view.window?.alphaValue = 0
        #expect(fixture.delegate.popover.isShown)
        #expect(fixture.model.open)
        #expect(fixture.model.selected == "anthropic/fixture-1")
        fixture.pointOutside()
        fixture.delegate.updateHover()
        try await Task.sleep(for: .seconds(6))
        #expect(fixture.delegate.popover.isShown)
        #expect(fixture.window.contentView?.bounds.width == 349)
        #expect(fixture.hostingWidth == 349)
        #expect(fixture.model.hoverExpanded)
        #expect(!fixture.model.fadingNormal)

        var closedWhileSwitching = false
        let opening = fixture.model.$open.dropFirst().sink { if !$0 { closedWhileSwitching = true } }
        fixture.pointInside(42)
        #expect(fixture.button.sendAction(fixture.button.action, to: fixture.button.target))
        #expect(fixture.model.selected == "anthropic/fixture-10")
        #expect(fixture.delegate.popover.isShown)
        #expect(fixture.delegate.popover.positioningRect.minX == 31)
        #expect(!closedWhileSwitching)
        opening.cancel()
        fixture.pointOutside()

        let closed = ContinuousClock.now
        fixture.model.closePopover()
        try await waitUntil("native popover did not close") { !fixture.model.open && !fixture.delegate.popover.isShown }
        #expect(fixture.window.contentView?.bounds.width == 349)
        #expect(fixture.hostingWidth == 349)
        var fadeStarted: ContinuousClock.Instant?
        let fade = fixture.model.$fadingNormal.sink { if $0 { fadeStarted = .now } }
        defer { fade.cancel() }
        try await waitUntil("native host did not become compact after popover close") { fixture.window.contentView?.bounds.width == 30 }
        #expect(closed.duration(to: try #require(fadeStarted)) >= .seconds(5))
        #expect(fixture.hostingWidth == 30)
    }

    @Test func reentryCancelsAnInFlightFadeBeforeItCanShrinkTheHost() async throws {
        let fixture = try await DelegateHost()
        defer { fixture.close() }
        fixture.enter()
        var reentered = false
        let fade = fixture.model.$fadingNormal.sink { value in
            guard value else { return }
            DispatchQueue.main.async {
                fixture.enter()
                reentered = true
            }
        }
        defer { fade.cancel() }
        fixture.leave()
        try await waitUntil("native hover did not reenter during the fade") { reentered }
        try await Task.sleep(for: .seconds(1))
        #expect(fixture.model.hoverExpanded)
        #expect(!fixture.model.fadingNormal)
        #expect(fixture.hostingWidth == 349)
        #expect(fixture.window.contentView?.bounds.width == 349)
    }
}

@MainActor
private final class DelegateHost {
    let cli: UsageCLI
    let model: StripModel
    let window: NSWindow
    let button: NSButton
    let delegate: AppDelegate
    private let pointer: Pointer

    var hostingWidth: CGFloat? {
        button.subviews.compactMap { $0 as? NSHostingView<StripView> }.first?.frame.width
    }

    init() async throws {
        _ = NSApplication.shared
        let cli = try UsageCLI()
        var complete = false
        defer { if !complete { cli.remove() } }
        let now = Date(timeIntervalSince1970: 1_790_985_600)
        try cli.succeedMany(takenAt: now)
        let model = StripModel(runner: Runner(binary: cli.binary), defaults: cli.defaults, now: { now })
        model.refresh()
        try await waitUntil { model.accounts.count == 12 }
        let window = NSWindow(contentRect: NSRect(x: -8000, y: 100, width: 300, height: 22),
                              styleMask: .borderless, backing: .buffered, defer: false)
        window.isReleasedWhenClosed = false
        window.alphaValue = 0
        let button = NSButton(frame: NSRect(x: 0, y: 0, width: 300, height: 22))
        window.contentView = button
        window.orderFront(nil)
        let pointer = Pointer()
        let delegate = AppDelegate(model: model, pointer: { pointer.position })
        delegate.mount(in: button) { [weak window, weak button] width in
            button?.frame.size.width = width
            window?.setContentSize(NSSize(width: width, height: 22))
        }
        delegate.popover.animates = false
        self.cli = cli
        self.model = model
        self.window = window
        self.button = button
        self.pointer = pointer
        self.delegate = delegate
        complete = true
    }

    func pointInside(_ x: CGFloat = 13) {
        pointer.position = window.convertPoint(toScreen: button.convert(NSPoint(x: x, y: 11), to: nil))
    }

    func pointOutside() {
        pointer.position = NSPoint(x: window.frame.maxX + 100, y: window.frame.maxY + 100)
    }

    func enter() {
        pointInside()
        delegate.updateHover()
    }

    func leave() {
        pointOutside()
        delegate.updateHover()
    }

    func close() {
        delegate.applicationWillTerminate(Notification(name: NSApplication.willTerminateNotification))
        window.close()
        cli.remove()
    }
}

@MainActor
private final class Pointer {
    var position = NSPoint.zero
}
