import Foundation
import Observation
import SporkCore

/// Runs blocking git work off the main thread.
func background<T>(_ work: @escaping () throws -> T) async throws -> T {
    try await Task.detached(priority: .userInitiated) { try work() }.value
}

struct AppAlert: Identifiable {
    let id = UUID()
    let title: String
    let message: String
    let detail: String
    var actions: [(String, () -> Void)] = []

    init(title: String, error: Error) {
        self.title = title
        if let g = error as? GitError {
            message = g.hint ?? "Git reported an error."
            detail = g.stderr
        } else {
            message = error.localizedDescription
            detail = ""
        }
    }

    init(title: String, message: String, actions: [(String, () -> Void)] = []) {
        self.title = title
        self.message = message
        self.detail = ""
        self.actions = actions
    }
}

struct FetchReport {
    var servers: [String: ServerStatus] = [:]
    var index: [String: String] = [:]              // Census.indexKey(repo, remote) -> server key
    var errors: [String: String] = [:]             // Census.indexKey(repo, remote) -> stderr
    var fetched = 0
    var finishedAt = Date()

    func context(for repo: String) -> (errors: [String: String], offline: [String]) {
        var errs: [String: String] = [:]
        var offline: [String] = []
        let prefix = repo + "\u{0}"
        for (k, v) in errors where k.hasPrefix(prefix) { errs[String(k.dropFirst(prefix.count))] = v }
        for (k, key) in index where k.hasPrefix(prefix) {
            if servers[key]?.online == false { offline.append(String(k.dropFirst(prefix.count))) }
        }
        return (errs, offline)
    }
}

let dashboardID = "__dashboard__"

@MainActor
@Observable
final class AppModel {
    static let shared = AppModel()

    var settings: SporkSettings
    let runner: GitRunner
    var summaries: [String: RepoSummary] = [:]
    var lastFetch: FetchReport?
    var fetching = false
    var selection: String? = dashboardID
    var alert: AppAlert?
    var toast: String?
    @ObservationIgnored private var repoModels: [String: RepoModel] = [:]
    @ObservationIgnored private var toastTask: Task<Void, Never>?

    private init() {
        settings = SporkSettings.load()
        runner = GitRunner()
        runner.askpass = try? Askpass.install(in: SporkSettings.supportDirectory)
        applySettings()
        refreshAllSummaries()
    }

    func applySettings() {
        runner.servers = settings.trustedServers
        runner.networkTimeout = TimeInterval(settings.fetchTimeout)
    }

    func save() {
        do { try settings.save() } catch {
            alert = AppAlert(title: "Couldn't Save Settings", error: error)
        }
        applySettings()
    }

    func show(_ message: String) {
        toast = message
        toastTask?.cancel()
        toastTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if !Task.isCancelled { self?.toast = nil }
        }
    }

    // MARK: repositories

    func repoModel(for path: String) -> RepoModel {
        if let m = repoModels[path] { return m }
        let m = RepoModel(app: self, path: path)
        repoModels[path] = m
        return m
    }

    /// Bookmark and select the repository that contains `path`.
    func open(path: String) {
        let runner = self.runner
        Task {
            let top = try? await background { Repository.findToplevel(runner: runner, path: path) }
            guard let top else {
                alert = AppAlert(title: "Not a Git Repository",
                                 message: "\(path) isn't inside a Git repository.")
                return
            }
            let b = settings.addBookmark(top)
            save()
            refreshSummary(b.path)
            selection = b.path
        }
    }

    func removeBookmark(_ path: String) {
        settings.removeBookmark(path)
        summaries[path] = nil
        repoModels[path] = nil
        if selection == path { selection = dashboardID }
        save()
    }

    func moveBookmark(_ path: String, by delta: Int) {
        settings.moveBookmark(path, by: delta)
        save()
    }

    func refreshSummary(_ path: String) {
        let runner = self.runner
        let ctx = lastFetch?.context(for: path) ?? ([:], [])
        Task {
            let s = try? await background {
                Census.summarize(path: path, runner: runner, fetchErrors: ctx.errors, offline: ctx.offline)
            }
            if let s { summaries[path] = s }
        }
    }

    func refreshAllSummaries() {
        for b in settings.bookmarks { refreshSummary(b.path) }
    }

    // MARK: census-style fetch

    func fetch(paths: [String]? = nil) async -> FetchReport? {
        guard !fetching else { return nil }
        fetching = true
        defer { fetching = false }
        let all = settings.bookmarks.map(\.path)
        let targets = paths ?? all
        let runner = self.runner
        let report: FetchReport? = try? await background {
            var report = FetchReport()
            var remotes: [String: [Remote]] = [:]
            for p in targets {
                if let r = try? Repository(runner: runner, path: p).remotes() { remotes[p] = r }
            }
            let (servers, index) = Census.groupServers(remotes)
            report.servers = Census.probeAll(servers)
            report.index = index
            final class Shared: @unchecked Sendable {
                var errors: [String: String] = [:]
                var failedHosts = Set<String>()
                var fetched = 0
                let lock = NSLock()
            }
            let shared = Shared()
            let jobs = remotes.flatMap { p, rs in rs.map { (p, $0.name) } }
            let servers2 = report.servers
            DispatchQueue.concurrentPerform(iterations: jobs.count) { i in
                let (p, name) = jobs[i]
                let ik = Census.indexKey(p, name)
                guard let key = index[ik], servers2[key]?.online == true else { return }
                shared.lock.lock()
                let skip = shared.failedHosts.contains(key)
                shared.lock.unlock()
                if skip { return }
                do {
                    try Repository(runner: runner, path: p).fetch(name)
                    shared.lock.lock(); shared.fetched += 1; shared.lock.unlock()
                } catch {
                    let g = error as? GitError
                    shared.lock.lock()
                    shared.errors[ik] = g?.stderr ?? error.localizedDescription
                    if let hint = g?.hint, hint.contains("didn't answer") || hint.contains("couldn't be resolved") {
                        shared.failedHosts.insert(key)
                    }
                    shared.lock.unlock()
                }
            }
            report.errors = shared.errors
            report.fetched = shared.fetched
            report.finishedAt = Date()
            return report
        }
        guard let report else { return nil }
        if paths != nil, var merged = lastFetch {
            merged.servers.merge(report.servers) { _, new in new }
            merged.index.merge(report.index) { _, new in new }
            merged.errors = merged.errors.filter { k, _ in !targets.contains { k.hasPrefix($0 + "\u{0}") } }
            merged.errors.merge(report.errors) { _, new in new }
            merged.finishedAt = report.finishedAt
            lastFetch = merged
        } else {
            lastFetch = report
        }
        for p in targets { refreshSummary(p) }
        return report
    }

    func fetchEverything() {
        Task {
            guard let r = await fetch() else { return }
            let offline = r.servers.values.filter { $0.online == false }.count
            var parts = ["Fetched \(Workspace.plural(r.fetched, "remote"))"]
            if offline > 0 { parts.append("\(Workspace.plural(offline, "server")) offline") }
            if !r.errors.isEmpty { parts.append("\(r.errors.count) failed") }
            show(parts.joined(separator: ", "))
            if let sel = selection, sel != dashboardID { repoModel(for: sel).refresh(full: true) }
        }
    }

    var counts: (total: Int, ok: Int, attention: Int) {
        var ok = 0, attention = 0
        for b in settings.bookmarks {
            switch summaries[b.path]?.level {
            case .ok: ok += 1
            case .attention, .missing: attention += 1
            default: break
            }
        }
        return (settings.bookmarks.count, ok, attention)
    }

    /// "repo: remote" -> URL for every bookmarked repository (certificate matching).
    func remoteURLsByLabel() async -> [String: String] {
        let runner = self.runner
        let bms = settings.bookmarks
        return (try? await background {
            var out: [String: String] = [:]
            for b in bms {
                for r in (try? Repository(runner: runner, path: b.path).remotes()) ?? [] {
                    out["\(b.displayName): \(r.name)"] = r.fetchURL
                }
            }
            return out
        }) ?? [:]
    }
}
