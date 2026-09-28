import Foundation

/// Runs the `unlimited` CLI. The app never reads a credential or calls a vendor; this is its
/// only source.
public struct Runner: Sendable {
    public static let minimumVersion = [0, 0, 23]  // roles even for limits an older install cached; 0.0.24 adds today's pace

    public let binary: URL

    public init(binary: URL) { self.binary = binary }

    /// A login-item launch gets no shell `PATH`, so look where installers put it.
    public static func locate(home: URL = FileManager.default.homeDirectoryForCurrentUser) -> Runner? {
        let candidates = [home.appending(path: ".local/bin/unlimited"),
                          URL(filePath: "/opt/homebrew/bin/unlimited"), URL(filePath: "/usr/local/bin/unlimited")]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0.path) }.map(Runner.init)
    }

    /// One entry of the machine's switch list (`unlimited off --json`): a target — a usage
    /// vendor, or with `account`, one account of it — and its end and note.
    public struct Switch: Codable, Sendable, Equatable, Hashable {
        public let target: String
        public let until: String?
        public let why: String?
        public let account: String?

        public init(target: String, until: String? = nil, why: String? = nil, account: String? = nil) {
            (self.target, self.until, self.why, self.account) = (target, until, why, account)
        }
    }

    /// The machine's switch list. An older `unlimited` does not know `--json`; the caller
    /// treats any failure as "none", never as a broken strip.
    public func switches(timeout: TimeInterval = 10) throws -> [Switch] {
        try JSONDecoder().decode([Switch].self, from: run(["off", "--json"], timeout: timeout))
    }

    /// Flip one switch through the CLI, keeping its own message for the caller to surface.
    /// `account` narrows it to one account of a usage vendor. Exit 1 means the flip was refused
    /// (`on` of a switch that is not off); the caller treats it as "already there", any other
    /// failure as a problem.
    public func setOffer(_ target: String, account: String? = nil, off: Bool, timeout: TimeInterval = 15) throws {
        try runCaptured((off ? ["off"] : ["on"]) + [target] + (account.map { ["--account", $0] } ?? []),
                        timeout: timeout)
    }

    /// A cold read asks every vendor in turn; the timeout leaves room for a slow one.
    public func read(maxAge: Int? = nil, timeout: TimeInterval = 45) throws -> [Reading] {
        try Reading.decode(run(["read", "--json"] + (maxAge.map { ["--max-age", String($0)] } ?? []), timeout: timeout))
    }

    /// Whether this install emits what the strip needs.
    public func isRecentEnough() -> Bool {
        guard let out = try? run(["--version"], timeout: 10),
              let v = Runner.version(String(decoding: out, as: UTF8.self)) else { return false }
        return v.lexicographicallyPrecedes(Runner.minimumVersion) == false
    }

    static func version(_ s: String) -> [Int]? {
        let parts = s.split(separator: " ").last?.trimmingCharacters(in: .whitespacesAndNewlines).split(separator: ".")
        let nums = parts?.compactMap { Int($0) }
        return nums?.count == 3 ? nums : nil
    }

    func run(_ args: [String], timeout: TimeInterval) throws -> Data {
        try runCaptured(args, timeout: timeout).0
    }

    /// The one process-plumbing copy: run the CLI, kill it at the timeout, keep its stderr for
    /// the error a caller may want to surface. Stderr goes to a file, not a pipe — a chatty
    /// child cannot then stall the stdout read until the timeout.
    func runCaptured(_ args: [String], timeout: TimeInterval) throws -> (Data, String) {
        let errPath = FileManager.default.temporaryDirectory.appending(path: "unlimited-\(UUID().uuidString).err")
        FileManager.default.createFile(atPath: errPath.path, contents: nil,
                                       attributes: [.posixPermissions: 0o600])
        let p = Process()
        p.executableURL = binary
        p.arguments = args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = try FileHandle(forWritingTo: errPath)
        try p.run()
        let killer = DispatchWorkItem { p.terminate() }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        killer.cancel()
        let message = String(decoding: (try? Data(contentsOf: errPath)) ?? Data(), as: UTF8.self)
        try? FileManager.default.removeItem(at: errPath)
        guard p.terminationStatus == 0 else { throw RunError.exit(p.terminationStatus, stderr: message) }
        return (data, message)
    }

    public enum RunError: Error {
        case exit(Int32, stderr: String)
    }
}
