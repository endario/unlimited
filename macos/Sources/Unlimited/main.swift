import AppKit
import Combine
import SwiftUI
import UnlimitedKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    private let model = StripModel()
    private var item: NSStatusItem!
    private var host: NSHostingView<StripView>!
    private var sizeWatch: Any?
    private let popover = NSPopover()
    /// Closes the popover on a click anywhere outside the app. It is not `.transient`: that
    /// would close it on the very click that switches accounts, and reopen it, a visible flicker.
    private var outside: Any?, escape: Any?
    private var hoverGlobal: Any?, hoverLocal: Any?
    private var hoverDelay = HoverDelay()
    private var hoverCollapse: Task<Void, Never>?
    private var geometryObservers: [NSObjectProtocol] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        host = NSHostingView(rootView: StripView(model: model))
        item.button?.addSubview(host)
        fit()
        // The strip's width follows its tiles.
        sizeWatch = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { self?.fit() }
        }
        popover.behavior = .applicationDefined
        popover.delegate = self
        popover.contentViewController = NSHostingController(rootView: PopoverView(model: model))
        model.closePopover = { [weak self] in self?.popover.performClose(nil) }
        item.button?.target = self
        item.button?.action = #selector(toggle)
        watchHover()
        model.start()
    }

    private func fit() {
        syncHoverMonitors()
        let layout = StripLayout(tiles: model.displayedTiles, spacing: Double(StripView.spacing), padding: Double(StripView.padding))
        let width = model.displayedTiles.last.map { layout.rect(of: $0).maxX + Double(StripView.padding) } ?? 0
        host.frame = NSRect(x: 0, y: 0, width: width, height: 22)
        item.length = width
        let summary = "\(model.tiles.count) normal accounts hidden. Show accounts."
        item.button?.setAccessibilityLabel(model.collapsedNormal ? summary : "Account usage")
        item.button?.toolTip = model.collapsedNormal ? "Normal accounts hidden automatically. Hover to show; click to open." : nil
        if popover.isShown, let button = item.button { popover.positioningRect = rect(of: button) }
    }

    private func syncHoverMonitors() {
        guard model.prefs.autoHideNormal else {
            for monitor in [hoverGlobal, hoverLocal].compactMap({ $0 }) { NSEvent.removeMonitor(monitor) }
            (hoverGlobal, hoverLocal) = (nil, nil)
            cancelHover()
            return
        }
        guard hoverGlobal == nil, hoverLocal == nil else { return }
        let events: NSEvent.EventTypeMask = [.mouseMoved, .leftMouseDragged, .rightMouseDragged, .otherMouseDragged,
                                             .leftMouseUp, .rightMouseUp, .otherMouseUp]
        hoverGlobal = NSEvent.addGlobalMonitorForEvents(matching: events) { [weak self] _ in
            Task { @MainActor in self?.updateHover() }
        }
        hoverLocal = NSEvent.addLocalMonitorForEvents(matching: events) { [weak self] event in
            Self.passThroughHoverEvent(event) { self?.updateHover() }
        }
    }

    private func watchHover() {
        if let window = host.window {
            for name in [NSWindow.didMoveNotification, NSWindow.didChangeScreenNotification] {
                geometryObservers.append(NotificationCenter.default.addObserver(
                    forName: name, object: window, queue: .main) { [weak self] _ in
                        Task { @MainActor in
                            if name == NSWindow.didChangeScreenNotification { self?.cancelHover() }
                            self?.updateHover()
                        }
                    })
            }
        }
        geometryObservers.append(NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in
                    self?.cancelHover()
                    self?.updateHover()
                }
            })
    }

    private func cancelHover() {
        hoverCollapse?.cancel()
        hoverCollapse = nil
        hoverDelay.reset()
        if model.hoverExpanded { model.hoverExpanded = false }
        if model.fadingNormal { model.fadingNormal = false }
    }

    static func passThroughHoverEvent(_ event: NSEvent, update: @escaping @MainActor () -> Void) -> NSEvent {
        DispatchQueue.main.async { update() }
        return event
    }

    static func hoverContains(_ point: NSPoint, in rect: NSRect) -> Bool {
        NSMouseInRect(point, rect, false)
    }

    private func updateHover() {
        guard model.prefs.autoHideNormal, let window = item.button?.window else { return }
        let inside = Self.hoverContains(NSEvent.mouseLocation, in: window.frame)
        hoverDelay.update(inside: inside, open: model.open, now: .now)
        if inside || model.open {
            hoverCollapse?.cancel()
            hoverCollapse = nil
            if model.fadingNormal { model.fadingNormal = false }
            if !model.hoverExpanded {
                let animation: Animation? = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? nil : .easeInOut(duration: StripView.fadeDuration)
                withAnimation(animation) { model.hoverExpanded = true }
                fit()
            }
        } else if let deadline = hoverDelay.collapseAt, hoverCollapse == nil {
            hoverCollapse = Task { [weak self] in
                do {
                    try await Task.sleep(until: deadline, clock: .continuous)
                    guard let self, self.hoverDelay.collapse(now: .now) else { return }
                    self.model.fadingNormal = true
                    if !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
                        try await Task.sleep(for: .seconds(StripView.fadeDuration))
                    }
                    try Task.checkCancellation()
                    self.model.hoverExpanded = false
                    self.model.fadingNormal = false
                    self.hoverCollapse = nil
                    self.fit()
                } catch {}
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        hoverCollapse?.cancel()
        for monitor in [hoverGlobal, hoverLocal, outside, escape].compactMap({ $0 }) { NSEvent.removeMonitor(monitor) }
        for observer in geometryObservers {
            NotificationCenter.default.removeObserver(observer)
            NSWorkspace.shared.notificationCenter.removeObserver(observer)
        }
    }

    /// The strip is the popover's tabs: a click opens the account under the pointer, a click on
    /// another switches to it, and a click on the open one closes it.
    @objc private func toggle() {
        // The pointer on screen: the current event can belong to the popover's own window.
        guard let button = item.button, let window = button.window else { return }
        fit()
        let x = button.convert(window.convertPoint(fromScreen: NSEvent.mouseLocation), from: nil).x
        let hit = model.displayedAccount(at: Double(x))
        if popover.isShown {
            if hit?.id == model.selected { return popover.performClose(nil) }
            model.selected = hit?.id
            popover.positioningRect = rect(of: button)  // moves the open popover; no close, no reopen
            return
        }
        model.selected = hit?.id
        model.refresh(maxAge: 60)
        anchor(button)
        popover.contentViewController?.view.window?.makeKey()
    }

    /// The selected tile, in the button's coordinates.
    private func rect(of button: NSStatusBarButton) -> NSRect {
        let selected = model.displayedTiles.first { $0.id == model.selected } ?? model.displayedTiles.first
        let layout = StripLayout(tiles: model.displayedTiles, spacing: Double(StripView.spacing), padding: Double(StripView.padding))
        let rect = selected.map(layout.rect(of:)) ?? .init(x: Double(StripView.padding), width: Double(TileView.width))
        return NSRect(x: rect.x, y: 0, width: rect.width, height: button.bounds.height)
    }

    private func anchor(_ button: NSStatusBarButton) {
        model.open = true
        updateHover()
        fit()
        popover.show(relativeTo: rect(of: button), of: button, preferredEdge: .minY)
        outside = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            Task { @MainActor in self?.popover.performClose(nil) }
        }
        escape = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] e in
            guard e.keyCode == 53 else { return e }  // Escape
            self?.popover.performClose(nil)
            return nil
        }
    }

    func popoverDidClose(_ notification: Notification) {
        model.open = false
        updateHover()
        fit()
        for m in [outside, escape].compactMap({ $0 }) { NSEvent.removeMonitor(m) }
        (outside, escape) = (nil, nil)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
