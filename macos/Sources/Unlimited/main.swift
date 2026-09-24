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
    /// A transient popover closes on the mouse-down that also clicks the strip; that click must
    /// not reopen it.
    private var closedAt = Date.distantPast

    func applicationDidFinishLaunching(_ notification: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        host = NSHostingView(rootView: StripView(model: model))
        item.button?.addSubview(host)
        fit()
        // The strip's width follows its tiles.
        sizeWatch = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { self?.fit() }
        }
        popover.behavior = .transient
        popover.delegate = self
        popover.contentViewController = NSHostingController(rootView: PopoverView(model: model))
        item.button?.target = self
        item.button?.action = #selector(toggle)
        model.start()
    }

    private func fit() {
        let size = host.fittingSize
        host.frame = NSRect(origin: .zero, size: size)
        item.length = size.width
    }

    /// Opens on the account under the pointer.
    @objc private func toggle() {
        if popover.isShown { return popover.performClose(nil) }
        guard Date().timeIntervalSince(closedAt) > 0.3 else { return }
        guard let button = item.button, let event = NSApp.currentEvent else { return }
        let x = button.convert(event.locationInWindow, from: nil).x
        model.selected = Tile.at(x, in: model.tiles, width: TileView.width, spacing: StripView.spacing,
                                 padding: StripView.padding)?.id
        model.refresh(maxAge: 60)
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
    }

    func popoverDidClose(_ notification: Notification) { closedAt = Date() }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
