import AppKit
import SwiftUI
import UnlimitedKit

@MainActor
final class StripModel: ObservableObject {
    @Published private(set) var tiles: [Tile] = [.waiting]
    @Published var hoverExpanded = false
    @Published var fadingNormal = false
    private static let normalSummary = Tile(id: "auto-hidden-normal", label: "", value: .waiting, dimmed: false)

    private var autoCollapsing: Bool { prefs.autoHideNormal && !hoverExpanded && !open && problem == nil }
    var collapsedNormal: Bool { autoCollapsing && !tiles.isEmpty && tiles.allSatisfy(\.isRoutine) }

    func fades(_ tile: Tile) -> Bool {
        fadingNormal && prefs.autoHideNormal && problem == nil && !open && tile.isRoutine
    }

    var displayedTiles: [Tile] {
        guard autoCollapsing else { return tiles }
        let shown = tiles.filter { !$0.isRoutine }
        return shown.isEmpty ? [Self.normalSummary] : shown
    }

    func displayedAccount(at x: Double) -> Tile? {
        let layout = StripLayout(tiles: displayedTiles, spacing: Double(StripView.spacing), padding: Double(StripView.padding))
        let hit = collapsedNormal ? tiles.first : layout.tile(at: x)
        return hit.flatMap { $0.id.isEmpty ? nil : $0 }
    }
    @Published private(set) var readings: [String: Reading] = [:]
    /// How to open each account's Claude Code, by tile id; absent where it has no wrapper.
    @Published private(set) var launches: [String: Launch] = [:]
    /// The account the popover shows, by tile id.
    @Published var selected: String?
    /// Whether the popover is showing, so the strip can mark the selected tile.
    @Published var open = false
    /// Closes the popover: set by the app, used by what the popover opens (Settings).
    var closePopover: () -> Void = {}
    /// Every account as read, a linked API credit folded into its Claude account, before the owner's hiding: what Settings lists.
    @Published private(set) var accounts: [Tile] = []
    @Published private(set) var prefs: Preferences

    /// The owner's name for an account where they gave one, else the label the strip made.
    func name(of tile: Tile) -> String { prefs.labels[tile.id] ?? tile.label }

    private static let prefsKey = "preferences", pathKey = "unlimitedPath"

    private static func loadPrefs(_ defaults: UserDefaults) -> Preferences {
        defaults.data(forKey: prefsKey)
            .flatMap { try? JSONDecoder().decode(Preferences.self, from: $0) } ?? Preferences()
    }

    /// Changes the owner's arrangement and redraws the strip from the last reading.
    func arrange(_ change: (inout Preferences) -> Void) {
        change(&prefs)
        save()
        redraw()
    }

    var customPath: String {
        get { defaults.string(forKey: Self.pathKey) ?? "" }
        set {
            defaults.set(newValue, forKey: Self.pathKey)
            runner = nil
            refresh()
        }
    }

    private var lastRead: [Reading] = []
    /// A refresh asked for while one was running, run when it finishes.
    private var queuedRefresh: (pending: Bool, maxAge: Int?) = (false, nil)
    private let now: () -> Date
    private let defaults: UserDefaults

    init(runner: Runner? = nil, defaults: UserDefaults = .standard, now: @escaping () -> Date = Date.init) {
        self.runner = runner
        self.defaults = defaults
        self.now = now
        self.prefs = Self.loadPrefs(defaults)
    }

    private func redraw() {
        guard !lastRead.isEmpty else { return }
        accounts = Tile.strip(ApiCredit.folded(lastRead), now: now())
        tiles = prefs.apply(accounts)
        if tiles.isEmpty { tiles = [.waiting] }
        save()
        pace()
    }

    private func save() {
        if let data = try? JSONEncoder().encode(prefs) { defaults.set(data, forKey: Self.prefsKey) }
    }
    @Published private(set) var problem: String?
    /// The strip's phase: each weekly figure for `weeklyShown`, then any alternate window for
    /// `alternateShown`. The timer runs only while some tile has an alternate.
    @Published private(set) var alternating = false
    static let weeklyShown: TimeInterval = 6, alternateShown: TimeInterval = 3
    private var phase: Timer?

    static let interval: TimeInterval = 120
    private var runner: Runner?
    private(set) var busy = false
    private var timer: Timer?
    private var versionChecked: Date?

    func start() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: Self.interval, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh(maxAge: Int? = nil) {
        if busy {
            // A warm first paint leaves data on screen while the live read runs; a refresh asked
            // for in that window still runs, after it, rather than being dropped.
            queuedRefresh = (true, maxAge)
            return
        }
        let custom = customPath
        runner = runner ?? (custom.isEmpty ? Runner.locate()  // installed after launch: found on the next tick
                                           : Runner(binary: URL(filePath: (custom as NSString).expandingTildeInPath)))
        guard let runner else { return fail("unlimited not found in ~/.local/bin, /opt/homebrew/bin or /usr/local/bin") }
        busy = true
        // The version is checked again only when the binary is replaced (an upgrade).
        let stamp = (try? FileManager.default.attributesOfItem(atPath: runner.binary.resolvingSymlinksInPath().path))?[.modificationDate] as? Date
        let checked = stamp != nil && stamp == versionChecked
        let warmStart = lastRead.isEmpty
        Task.detached {
            let recent = checked || runner.isRecentEnough()
            await MainActor.run { if recent { self.versionChecked = stamp } }
            // First paint: the CLI's cached readings answer at once while a cold vendor read runs.
            if recent, warmStart, (maxAge ?? 300) < 86_400,
               let readings = try? runner.read(maxAge: 86_400) {
                await MainActor.run {
                    self.problem = nil
                    self.lastRead = readings
                    self.redraw()
                    self.readings = Dictionary(readings.map { ("\($0.vendor)/\($0.account ?? "")", $0) },
                                               uniquingKeysWith: { a, _ in a })
                    self.launches = self.readings.compactMapValues { Launch.resolve(names: $0.names) }
                }
            }
            let result: Result<[Reading], Error> = recent ? Result { try runner.read(maxAge: maxAge) } : .failure(Problem.tooOld)
            await MainActor.run {
                self.busy = false
                switch result {
                case .success(let readings):
                    self.problem = nil
                    self.lastRead = readings
                    self.redraw()
                    self.readings = Dictionary(readings.map { ("\($0.vendor)/\($0.account ?? "")", $0) },
                                               uniquingKeysWith: { a, _ in a })
                    self.launches = self.readings.compactMapValues { Launch.resolve(names: $0.names) }
                case .failure(let e as Reading.SchemaError):
                    self.fail("unlimited speaks schema \(e.schema); this app reads schema 1")
                case .failure(Problem.tooOld):
                    self.fail("unlimited is older than 0.0.23: run `uv tool install --force unlimited`")
                case .failure:
                    if self.tiles == [.waiting] { self.fail("unlimited read failed") }
                    else {
                        self.problem = "unlimited read failed"
                        self.redraw()
                    }
                }
                let queued = self.queuedRefresh
                self.queuedRefresh = (false, nil)
                if queued.pending { self.refresh(maxAge: queued.maxAge) }
            }
        }
    }

    private func pace() {
        let needed = tiles.contains { $0.alternate != nil }
        guard needed != (phase != nil) else { return }
        phase?.invalidate()
        phase = nil
        alternating = false
        if needed { schedule(weeklyFor: Self.weeklyShown) }
    }

    private func schedule(weeklyFor delay: TimeInterval) {
        phase = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.phase != nil else { return }
                withAnimation(.easeInOut(duration: 0.4)) { self.alternating.toggle() }
                self.schedule(weeklyFor: self.alternating ? Self.alternateShown : Self.weeklyShown)
            }
        }
    }

    private func fail(_ why: String) {
        problem = why
        tiles = [.broken]
    }

    enum Problem: Error { case tooOld }
}
