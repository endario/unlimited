import AppKit
import Combine
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let model = StripModel()
    private var item: NSStatusItem!
    private var host: NSHostingView<StripView>!
    private var sizeWatch: Any?

    func applicationDidFinishLaunching(_ notification: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        host = NSHostingView(rootView: StripView(model: model))
        item.button?.addSubview(host)
        fit()
        // The strip's width follows its tiles.
        sizeWatch = model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { self?.fit() }
        }
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        model.start()
    }

    private func fit() {
        let size = host.fittingSize
        host.frame = NSRect(origin: .zero, size: size)
        item.length = size.width
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        if let why = model.problem {
            menu.addItem(withTitle: why, action: nil, keyEquivalent: "")
            menu.addItem(.separator())
        }
        menu.addItem(withTitle: "Refresh", action: #selector(refresh), keyEquivalent: "r").target = self
        menu.addItem(withTitle: "Quit Unlimited", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
    }

    @objc private func refresh() { model.refresh() }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = AppDelegate()
app.delegate = delegate
app.run()
