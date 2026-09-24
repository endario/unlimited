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
        model.start()
    }

    private func fit() {
        let size = host.fittingSize
        host.frame = NSRect(origin: .zero, size: size)
        item.length = size.width
    }

    /// The strip is the popover's tabs: a click opens the account under the pointer, a click on
    /// another switches to it, and a click on the open one closes it.
    @objc private func toggle() {
        // The pointer on screen: the current event can belong to the popover's own window.
        guard let button = item.button, let window = button.window else { return }
        let x = button.convert(window.convertPoint(fromScreen: NSEvent.mouseLocation), from: nil).x
        let hit = Tile.at(x, in: model.tiles, width: TileView.width, spacing: StripView.spacing,
                          padding: StripView.padding)
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
        let i = model.tiles.firstIndex { $0.id == model.selected } ?? 0
        let x = StripView.padding + CGFloat(i) * (TileView.width + StripView.spacing)
        return NSRect(x: x, y: 0, width: TileView.width, height: button.bounds.height)
    }

    private func anchor(_ button: NSStatusBarButton) {
        popover.show(relativeTo: rect(of: button), of: button, preferredEdge: .minY)
        model.open = true
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
        for m in [outside, escape].compactMap({ $0 }) { NSEvent.removeMonitor(m) }
        (outside, escape) = (nil, nil)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
