import Foundation
import Observation
import SporkCore

enum DetailTab: String, CaseIterable, Identifiable {
    case commit, changes, browse
    var id: String { rawValue }
    var title: String {
        switch self {
        case .commit: return "Commit"
        case .changes: return "Changes"
        case .browse: return "Browse"
        }
    }
}

enum BrowseMode: String, CaseIterable, Identifiable {
    case contents, blame, history
    var id: String { rawValue }
    var title: String { rawValue.capitalized }
}

enum BlobContent {
    case none
    case text(String)
    case image(Data)
    case binary(Int)
    case tooLarge(Int)
    case submodule(String)
}

/// Identifies a row in the Changes file list (the same path can be staged and unstaged).
struct ChangeKey: Hashable {
    let path: String
    let staged: Bool
}

/// One open repository: everything the commit list and detail pane show.
@MainActor
@Observable
final class RepoModel {
    unowned let app: AppModel
    let path: String
    let repo: Repository

    // repository state
    var status: WorkingStatus?
    var branches: [Branch] = []
    var remotes: [Remote] = []
    var remoteStates: [RemoteBranchState] = []
    var identity = ""
    var operation: String?
    var branch: String? { status?.branch.head }

    // commit list
    var commits: [Commit] = []
    var graph: [GraphRow] = []
    var maxLanes = 1
    var exhausted = true
    var loadingMore = false
    var searchText = ""
    var searchField: SearchField = .any
    var selection: String?
    @ObservationIgnored var graphBuilder = GraphBuilder()
    static let page = 300

    // detail pane
    var tab: DetailTab = .commit
    var detail: Commit?
    var changes: [FileChange] = []
    var changesFor: String?
    var selectedChange: ChangeKey?
    var diffs: [FileDiff] = []
    var diffLoading = false

    // browse
    var tree: [TreeEntry] = []
    var treeRev: String?
    var browsePath: String?
    var browseMode: BrowseMode = .contents
    var expanded: Set<String> = []
    var blob: BlobContent = .none
    var blame: [BlameLine] = []
    var history: [Commit] = []

    // composer
    var summary = ""
    var message = ""
    var amend = false

    var busy = 0
    @ObservationIgnored private var tokens: [String: Int] = [:]

    init(app: AppModel, path: String) {
        self.app = app
        self.path = path
        self.repo = Repository(runner: app.runner, path: path)
        refresh(full: true)
    }

    var name: String { (path as NSString).lastPathComponent }
    var showsWorkingRow: Bool { !(status?.isClean ?? true) && searchText.isEmpty }
    var defaultRemote: String? {
        if let up = status?.branch.upstream, let slash = up.firstIndex(of: "/") {
            let n = String(up[..<slash])
            if remotes.contains(where: { $0.name == n }) { return n }
        }
        return remotes.first?.name
    }

    var fetchContext: FetchContext {
        app.lastFetch?.context(for: path) ?? noFetchContext
    }

    // MARK: plumbing

    private func token(_ key: String) -> Int {
        tokens[key, default: 0] += 1
        return tokens[key]!
    }

    private func isCurrent(_ key: String, _ t: Int) -> Bool { tokens[key] == t }

    /// Run `work` in the background; drop stale results; report errors.
    private func run<T>(_ key: String, title: String?, busy showBusy: Bool = false,
                        _ work: @escaping () throws -> T, done: @escaping (T) -> Void) {
        let t = token(key)
        if showBusy { busy += 1 }
        Task {
            do {
                let value = try await offMain(work)
                if showBusy { busy -= 1 }
                if isCurrent(key, t) { done(value) }
            } catch {
                if showBusy { busy -= 1 }
                if isCurrent(key, t), let title { app.alert = AppAlert(title: title, error: error) }
            }
        }
    }

    // MARK: loading

    func refresh(full: Bool = false) {
        let repo = self.repo
        run("refresh", title: "Couldn't Read Repository", {
            let st = try repo.status()
            let rs = try repo.remotes()
            return (st, try repo.branches(), rs, try repo.remoteStates(branch: st.branch.head, remotes: rs),
                    repo.identity(), repo.operationInProgress())
        }, done: { [self] r in
            let oldHead = status?.branch.oid
            let oldBranch = branch
            status = r.0
            branches = r.1
            remotes = r.2
            remoteStates = r.3
            identity = r.4
            operation = r.5
            if full || oldHead != r.0.branch.oid || oldBranch != r.0.branch.head {
                reloadLog()
            } else if selection == workingCopyID {
                if r.0.isClean { reloadLog() } else { loadTab() }
            }
            app.refreshSummary(path)
        })
    }

    private var query: LogQuery {
        LogQuery(allBranches: app.settings.showAllBranches, text: searchText, field: searchField,
                 limit: Self.page)
    }

    func reloadLog() {
        let q = query
        let repo = self.repo
        let keep = selection
        run("log", title: "Couldn't Load History", { try repo.log(q) }, done: { [self] list in
            graphBuilder = GraphBuilder()
            commits = list
            graph = graphBuilder.add(list)
            maxLanes = min(graph.map(\.width).max() ?? 1, 10)
            exhausted = list.count < Self.page || !searchText.isEmpty
            if let keep, keep == workingCopyID, showsWorkingRow {
                select(workingCopyID)
            } else if let keep, keep != workingCopyID, list.contains(where: { $0.sha == keep }) {
                select(keep)
            } else if showsWorkingRow {
                select(workingCopyID)
            } else {
                select(list.first?.sha)
            }
        })
    }

    func loadMore() {
        guard !exhausted, !loadingMore else { return }
        var q = query
        q.skip = commits.count
        loadingMore = true
        let repo = self.repo
        run("more", title: nil, { try repo.log(q) }, done: { [self] list in
            loadingMore = false
            commits += list
            let rows = graphBuilder.add(list)
            graph += rows
            maxLanes = min(max(maxLanes, rows.map(\.width).max() ?? 1), 10)
            exhausted = list.count < Self.page
        })
    }

    func select(_ id: String?) {
        if selection != id { selection = id }
        loadTab()
    }

    func loadTab() {
        guard let sel = selection else { return }
        let repo = self.repo
        if sel == workingCopyID {
            switch tab {
            case .commit:
                break  // the composer reads `status` directly
            case .changes:
                changesFor = workingCopyID
                if let st = status {
                    let keys = st.staged.map { ChangeKey(path: $0.path, staged: true) }
                        + st.unstaged.map { ChangeKey(path: $0.path, staged: false) }
                    if selectedChange == nil || !keys.contains(selectedChange!) { selectedChange = keys.first }
                    loadDiff()
                }
            case .browse:
                if let head = status?.branch.oid { loadTree(head) } else { tree = []; treeRev = nil }
            }
            return
        }
        switch tab {
        case .commit:
            run("detail", title: "Couldn't Load Commit", {
                (try repo.commitDetails(sel), try repo.commitChanges(sel))
            }, done: { [self] r in
                detail = r.0
                changes = r.1
                changesFor = sel
            })
        case .changes:
            if changesFor == sel {
                loadDiff()
                return
            }
            run("changes", title: "Couldn't Load Changes", { try repo.commitChanges(sel) }, done: { [self] ch in
                changes = ch
                changesFor = sel
                selectedChange = ch.first.map { ChangeKey(path: $0.path, staged: false) }
                loadDiff()
            })
        case .browse:
            loadTree(sel)
        }
    }

    func loadDiff() {
        guard let sel = selection, let key = selectedChange else {
            diffs = []
            return
        }
        let repo = self.repo
        diffLoading = true
        if sel == workingCopyID {
            guard let entry = status?.entries.first(where: { $0.path == key.path }) else {
                diffs = []
                return
            }
            run("diff", title: "Couldn't Load Diff", {
                if key.staged { return try repo.stagedDiff(entry.path, oldPath: entry.oldPath) }
                return try repo.unstagedDiff(entry.path, untracked: entry.untracked)
            }, done: { [self] d in diffs = d; diffLoading = false })
        } else {
            guard let change = changes.first(where: { $0.path == key.path }) else { return }
            run("diff", title: "Couldn't Load Diff", { try repo.commitDiff(sel, change: change) },
                done: { [self] d in diffs = d; diffLoading = false })
        }
    }

    func loadTree(_ rev: String, reveal: String? = nil) {
        let repo = self.repo
        if treeRev == rev && reveal == nil { return }
        run("tree", title: "Couldn't List Files", { try repo.tree(rev) }, done: { [self] entries in
            tree = entries
            treeRev = rev
            let target = reveal ?? browsePath
            if let target, entries.contains(where: { $0.path == target }) {
                revealInTree(target)
            } else {
                browsePath = nil
                blob = .none
            }
        })
    }

    func revealInTree(_ path: String) {
        var parts = path.split(separator: "/").map(String.init)
        parts.removeLast()
        var acc = ""
        for p in parts {
            acc = acc.isEmpty ? p : acc + "/" + p
            expanded.insert(acc)
        }
        openFile(path)
    }

    func openFile(_ path: String?) {
        if browsePath != path { browsePath = path }
        guard let path, let rev = treeRev, let entry = tree.first(where: { $0.path == path }) else {
            blob = .none
            return
        }
        let repo = self.repo
        switch browseMode {
        case .contents:
            if entry.kind == "commit" { blob = .submodule(entry.sha); return }
            if let size = entry.size, size > maxBlobBytes { blob = .tooLarge(size); return }
            run("blob", title: "Couldn't Read File", { try repo.blob(rev, path: path) }, done: { [self] data in
                let ext = (path as NSString).pathExtension.lowercased()
                if ["png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff", "tif", "heic", "ico", "icns", "pdf"].contains(ext) {
                    blob = .image(data)
                } else if Parsers.isProbablyBinary(data) {
                    blob = .binary(data.count)
                } else {
                    blob = .text(String(decoding: data, as: UTF8.self))
                }
            })
        case .blame:
            if let size = entry.size, size > maxBlobBytes { blob = .tooLarge(size); return }
            run("blame", title: "Couldn't Load Blame", { try repo.blame(path, rev: rev) },
                done: { [self] b in blame = b })
        case .history:
            run("history", title: "Couldn't Load History", { try repo.fileHistory(path, rev: rev) },
                done: { [self] h in history = h })
        }
    }

    /// Jump to a commit from a parent link or file history.
    func openCommit(_ sha: String) {
        tab = .commit
        select(sha)
    }

    func showHistory(of path: String) {
        browseMode = .history
        tab = .browse
        let rev = selection == workingCopyID ? (status?.branch.oid ?? "HEAD") : (selection ?? "HEAD")
        if treeRev == rev { revealInTree(path) } else { loadTree(rev, reveal: path) }
    }

    // MARK: working copy

    private func afterChange() { refresh() }

    func stage(_ paths: [String]) {
        let repo = self.repo
        run("stage", title: "Couldn't Stage", { try repo.stage(paths) }, done: { [self] _ in afterChange() })
    }

    func unstage(_ paths: [String]) {
        let repo = self.repo
        run("stage", title: "Couldn't Unstage", { try repo.unstage(paths) }, done: { [self] _ in afterChange() })
    }

    func stageHunk(_ fd: FileDiff, _ i: Int) {
        let repo = self.repo
        run("stage", title: "Couldn't Stage Hunk", { try repo.stageHunk(fd, i) }, done: { [self] _ in afterChange() })
    }

    func unstageHunk(_ fd: FileDiff, _ i: Int) {
        let repo = self.repo
        run("stage", title: "Couldn't Unstage Hunk", { try repo.unstageHunk(fd, i) }, done: { [self] _ in afterChange() })
    }

    func discardHunk(_ fd: FileDiff, _ i: Int) {
        let repo = self.repo
        app.alert = AppAlert(title: "Discard This Hunk?",
                             message: "The change is removed from the file on disk. This can't be undone.",
                             actions: [("Discard", { [self] in
                                 run("stage", title: "Couldn't Discard", { try repo.discardHunk(fd, i) },
                                     done: { [self] _ in afterChange() })
                             })])
    }

    func discard(_ paths: [String]) {
        guard let st = status else { return }
        let entries = st.entries.filter { paths.contains($0.path) }
        let untracked = entries.filter(\.untracked).map(\.path)
        let tracked = entries.filter { !$0.untracked }.map(\.path)
        let repo = self.repo
        let names = paths.prefix(3).map { ($0 as NSString).lastPathComponent }.joined(separator: ", ")
        app.alert = AppAlert(
            title: "Discard Changes to \(names)\(paths.count > 3 ? "…" : "")?",
            message: untracked.isEmpty ? "Your changes will be lost. This can't be undone."
                : "Untracked files are moved to the Trash; changes to other files are lost.",
            actions: [("Discard", { [self] in
                run("stage", title: "Couldn't Discard", { try repo.discard(tracked: tracked, untracked: untracked) },
                    done: { [self] _ in afterChange() })
            })])
    }

    var canCommit: Bool {
        guard let st = status, st.conflicted.isEmpty else { return false }
        return !summary.trimmingCharacters(in: .whitespaces).isEmpty && (!st.staged.isEmpty || amend)
    }

    func commit() {
        guard canCommit else { return }
        let body = message.trimmingCharacters(in: .whitespacesAndNewlines)
        let msg = summary.trimmingCharacters(in: .whitespaces) + (body.isEmpty ? "" : "\n\n" + body) + "\n"
        let amending = amend
        let repo = self.repo
        run("commit", title: "Couldn't Commit", busy: true, { try repo.commit(message: msg, amend: amending) },
            done: { [self] _ in
                summary = ""
                message = ""
                amend = false
                selection = nil
                app.show(amending ? "Commit amended" : "Committed")
                refresh(full: true)
            })
    }

    func setAmend(_ on: Bool) {
        amend = on
        guard on, summary.isEmpty else { return }
        let repo = self.repo
        run("lastmsg", title: nil, { repo.lastCommitMessage() }, done: { [self] text in
            let parts = text.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: false)
            summary = parts.first.map(String.init) ?? ""
            message = parts.count > 1 ? String(parts[1]).trimmingCharacters(in: .whitespacesAndNewlines) : ""
        })
    }

    // MARK: branches

    func switchBranch(_ name: String) {
        guard name != branch else { return }
        let repo = self.repo
        run("switch", title: "Couldn't Switch to \(name)", busy: true, { try repo.switchTo(name) },
            done: { [self] _ in app.show("Switched to \(name)"); refresh(full: true) })
    }

    func createBranch(_ name: String, at start: String?) {
        let repo = self.repo
        run("switch", title: "Couldn't Create Branch", busy: true, { try repo.createBranch(name, start: start) },
            done: { [self] _ in app.show("Created \(name)"); refresh(full: true) })
    }

    func deleteBranch(_ name: String, force: Bool = false) {
        let repo = self.repo
        let t = token("delete")
        Task {
            do {
                try await offMain { try repo.deleteBranch(name, force: force) }
                guard isCurrent("delete", t) else { return }
                app.show("Deleted \(name)")
                refresh()
            } catch let e as GitError where !force && e.stderr.contains("not fully merged") {
                app.alert = AppAlert(title: "\(name) Isn't Merged",
                                     message: "Its commits aren't on any other branch and will be hard to recover.",
                                     actions: [("Delete Anyway", { [self] in deleteBranch(name, force: true) })])
            } catch {
                app.alert = AppAlert(title: "Couldn't Delete Branch", error: error)
            }
        }
    }

    // MARK: remotes

    func fetch(_ remote: String?) {
        if let remote {
            let repo = self.repo
            run("fetch", title: "Couldn't Fetch from \(remote)", busy: true, { try repo.fetch(remote) },
                done: { [self] _ in app.show("Fetched \(remote)"); refresh(full: true) })
            return
        }
        busy += 1
        Task {
            let report = await app.fetch(paths: [path])
            busy -= 1
            if let report {
                let ctx = report.context(for: path)
                if let first = ctx.errors.first {
                    app.alert = AppAlert(title: "Couldn't Fetch from \(first.key)",
                                         error: GitError(args: ["fetch"], code: 1, stderr: first.value))
                } else if !ctx.offline.isEmpty {
                    app.show("Fetched; offline: \(ctx.offline.joined(separator: ", "))")
                } else {
                    app.show("Fetched all remotes")
                }
            }
            refresh(full: true)
        }
    }

    func pull(_ remote: String?, mode: PullMode = .ffOnly) {
        guard let remote = remote ?? defaultRemote, let branch else { return }
        let repo = self.repo
        busy += 1
        Task {
            do {
                try await offMain { try repo.pull(remote, branch: branch, mode: mode) }
                busy -= 1
                app.show("Pulled from \(remote)")
            } catch let e as GitError where mode == .ffOnly &&
                        (e.stderr.lowercased().contains("not possible to fast-forward") || e.stderr.lowercased().contains("diverg")) {
                busy -= 1
                app.alert = AppAlert(title: "Branches Have Diverged",
                                     message: "Both \(branch) and \(remote)/\(branch) have new commits. Merge creates a merge commit; Rebase replays your commits on top.",
                                     actions: [("Merge", { [self] in pull(remote, mode: .merge) }),
                                               ("Rebase", { [self] in pull(remote, mode: .rebase) })])
            } catch {
                busy -= 1
                app.alert = AppAlert(title: "Couldn't Pull from \(remote)", error: error)
            }
            refresh(full: true)
        }
    }

    func push(_ targets: [String]) {
        guard let branch, !targets.isEmpty else { return }
        let setUpstream = status?.branch.upstream == nil && targets.count == 1
        let repo = self.repo
        busy += 1
        Task {
            let results: [(String, Error?)] = (try? await offMain {
                targets.map { r -> (String, Error?) in
                    do { try repo.push(r, branch: branch, setUpstream: setUpstream); return (r, nil) }
                    catch { return (r, error) }
                }
            }) ?? []
            busy -= 1
            let failed = results.filter { $0.1 != nil }
            let ok = results.filter { $0.1 == nil }.map(\.0)
            if let first = failed.first, let err = first.1 {
                app.alert = AppAlert(title: "Couldn't Push to \(failed.map(\.0).joined(separator: ", "))", error: err)
            }
            if !ok.isEmpty { app.show("Pushed \(branch) to \(ok.joined(separator: ", "))") }
            refresh()
        }
    }
}
