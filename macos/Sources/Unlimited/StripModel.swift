import AppKit
import UnlimitedKit

@MainActor
final class StripModel: ObservableObject {
    @Published private(set) var tiles: [Tile] = [.waiting]
    /// Why the strip is a single `!`, for the menu; nil when it reads.
    @Published private(set) var problem: String?

    static let interval: TimeInterval = 120
    private var runner: Runner?
    private var busy = false
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

    func refresh() {
        guard !busy else { return }
        runner = runner ?? Runner.locate()  // installed after launch: found on the next tick
        guard let runner else { return fail("unlimited not found in ~/.local/bin, /opt/homebrew/bin or /usr/local/bin") }
        busy = true
        // The version is checked again only when the binary is replaced (an upgrade).
        let stamp = (try? FileManager.default.attributesOfItem(atPath: runner.binary.resolvingSymlinksInPath().path))?[.modificationDate] as? Date
        let checked = stamp != nil && stamp == versionChecked
        Task.detached {
            let recent = checked || runner.isRecentEnough()
            let result: Result<[Reading], Error> = recent ? Result { try runner.read() } : .failure(Problem.tooOld)
            await MainActor.run { if recent { self.versionChecked = stamp } }
            await MainActor.run {
                self.busy = false
                switch result {
                case .success(let readings):
                    self.problem = nil
                    self.tiles = Tile.strip(readings, now: Date())
                case .failure(let e as Reading.SchemaError):
                    self.fail("unlimited speaks schema \(e.schema); this app reads schema 1")
                case .failure(Problem.tooOld):
                    self.fail("unlimited is older than 0.0.21: run `uv tool install --force unlimited`")
                case .failure:
                    // One failed run keeps the last strip; the next tick tries again.
                    if self.tiles == [.waiting] { self.fail("unlimited read failed") }
                }
            }
        }
    }

    private func fail(_ why: String) {
        problem = why
        tiles = [.broken]
    }

    enum Problem: Error { case tooOld }
}
