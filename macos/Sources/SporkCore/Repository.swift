import Foundation

public enum SearchField: String, CaseIterable, Sendable, Identifiable {
    case any, message, author, hash, path
    public var id: String { rawValue }
    public var title: String {
        switch self {
        case .any: return "Message or Author"
        case .message: return "Message"
        case .author: return "Author"
        case .hash: return "Hash"
        case .path: return "Path"
        }
    }
}

public struct LogQuery: Sendable {
    public var allBranches: Bool
    public var text: String
    public var field: SearchField
    public var limit: Int
    public var skip: Int
    public var path: String?

    public init(allBranches: Bool = false, text: String = "", field: SearchField = .any,
                limit: Int = 300, skip: Int = 0, path: String? = nil) {
        self.allBranches = allBranches
        self.text = text
        self.field = field
        self.limit = limit
        self.skip = skip
        self.path = path
    }
}

public let maxBlobBytes = 4 * 1024 * 1024

public enum PullMode: String, Sendable { case ffOnly = "--ff-only", merge = "--no-rebase", rebase = "--rebase" }

/// High-level operations on one repository. All methods block.
public final class Repository: @unchecked Sendable {
    public let runner: GitRunner
    public let path: String

    public init(runner: GitRunner, path: String) {
        self.runner = runner
        self.path = path
    }

    public var name: String { (path as NSString).lastPathComponent }

    @discardableResult
    public func git(_ args: String..., input: Data? = nil, check: Bool = true, network: Bool = false,
                    timeout: TimeInterval? = nil, okCodes: Set<Int32> = [0]) throws -> GitResult {
        try runner.run(args, cwd: path, input: input, check: check, network: network,
                       timeout: timeout, okCodes: okCodes)
    }

    public func gitArgs(_ args: [String], input: Data? = nil, check: Bool = true,
                        network: Bool = false, okCodes: Set<Int32> = [0]) throws -> GitResult {
        try runner.run(args, cwd: path, input: input, check: check, network: network, okCodes: okCodes)
    }

    public static func findToplevel(runner: GitRunner, path: String) -> String? {
        var isDir: ObjCBool = false
        var dir = path
        if FileManager.default.fileExists(atPath: path, isDirectory: &isDir), !isDir.boolValue {
            dir = (path as NSString).deletingLastPathComponent
        }
        guard let r = try? runner.run(["rev-parse", "--show-toplevel"], cwd: dir) else { return nil }
        let top = r.text.trimmingCharacters(in: .whitespacesAndNewlines)
        return top.isEmpty ? nil : top
    }

    // MARK: state

    public func operationInProgress() -> String? {
        guard let gd = try? git("rev-parse", "--absolute-git-dir").text
            .trimmingCharacters(in: .whitespacesAndNewlines) else { return nil }
        let checks = [("rebase-merge", "rebase"), ("rebase-apply/applying", "am"),
                      ("rebase-apply", "rebase"), ("MERGE_HEAD", "merge"),
                      ("CHERRY_PICK_HEAD", "cherry-pick"), ("REVERT_HEAD", "revert"),
                      ("BISECT_LOG", "bisect")]
        for (name, label) in checks
        where FileManager.default.fileExists(atPath: (gd as NSString).appendingPathComponent(name)) {
            return label
        }
        return nil
    }

    public func status() throws -> WorkingStatus {
        Parsers.parseStatusV2(try git("status", "--porcelain=v2", "--branch", "-z",
                                      "--untracked-files=all").text)
    }

    public func stashCount() -> Int {
        guard let r = try? git("rev-list", "--walk-reflogs", "--count", "refs/stash", check: false),
              r.code == 0 else { return 0 }
        return Int(r.text.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
    }

    public func headSHA() -> String? {
        guard let r = try? git("rev-parse", "--verify", "-q", "HEAD", check: false), r.code == 0 else { return nil }
        let s = r.text.trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? nil : s
    }

    public func currentBranch() -> String? {
        guard let r = try? git("symbolic-ref", "-q", "--short", "HEAD", check: false), r.code == 0 else { return nil }
        let s = r.text.trimmingCharacters(in: .whitespacesAndNewlines)
        return s.isEmpty ? nil : s
    }

    // MARK: history

    public func log(_ q: LogQuery) throws -> [Commit] {
        let text = q.text.trimmingCharacters(in: .whitespaces)
        if !text.isEmpty && q.field == .hash { return logByHash(text) }
        if !text.isEmpty && q.field == .any {
            // git ANDs --grep with --author, so "any" runs both and merges.
            var seen = Set<String>()
            var merged: [Commit] = []
            for f in [SearchField.message, .author] {
                var sub = q
                sub.field = f
                sub.skip = 0
                for c in try log(sub) where !seen.contains(c.sha) {
                    seen.insert(c.sha)
                    merged.append(c)
                }
            }
            merged.sort { $0.committerTime > $1.committerTime }
            if looksLikeHash(text) {
                merged = logByHash(text).filter { !seen.contains($0.sha) } + merged
            }
            return Array(merged.prefix(q.limit))
        }
        var args = ["log", "--topo-order", "--decorate=full", "--format=\(Parsers.logFormat)",
                    "--max-count=\(q.limit)"]
        if q.skip > 0 { args.append("--skip=\(q.skip)") }
        var path = q.path
        if !text.isEmpty {
            switch q.field {
            case .author: args += ["-i", "--fixed-strings", "--author=\(text)"]
            case .message: args += ["-i", "--fixed-strings", "--grep=\(text)"]
            case .path: path = text
            default: break
            }
        }
        if q.allBranches {
            args += ["--branches", "--remotes", "--tags", "HEAD"]
        } else if headSHA() == nil {
            return []
        }
        if let path { args += ["--", path] }
        let r = try gitArgs(args, check: false)
        if r.code != 0 {
            if headSHA() == nil { return [] }
            throw GitError(args: args, code: r.code, stderr: r.err)
        }
        return Parsers.parseLog(r.text)
    }

    func logByHash(_ text: String) -> [Commit] {
        guard let r = try? git("rev-parse", "--verify", "-q", "\(text)^{commit}", check: false),
              r.code == 0 else { return [] }
        let sha = r.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let out = try? git("log", "--decorate=full", "--format=\(Parsers.logFormat)", "-n", "1", sha)
        else { return [] }
        return Parsers.parseLog(out.text)
    }

    public func commitDetails(_ sha: String) throws -> Commit {
        let r = try git("show", "-s", "--decorate=full", "--format=\(Parsers.detailFormat)", sha)
        guard let c = Parsers.parseLog(r.text, withBody: true).first else {
            throw GitError(args: ["show", sha], code: 1, stderr: "Commit \(sha) not found")
        }
        return c
    }

    public func commitChanges(_ sha: String) throws -> [FileChange] {
        let base = ["diff-tree", "-r", "-z", "-M", "--root", "--no-commit-id", "--diff-merges=first-parent"]
        let ns = try gitArgs(base + ["--name-status", sha]).text
        let st = try gitArgs(base + ["--numstat", sha]).text
        return Parsers.mergeNumstat(Parsers.parseNameStatus(ns), Parsers.parseNumstat(st))
    }

    public func commitDiff(_ sha: String, change: FileChange) throws -> [FileDiff] {
        var paths = [change.path]
        if let old = change.oldPath, old != change.path { paths.insert(old, at: 0) }
        let r = try gitArgs(["diff-tree", "-p", "-M", "--root", "--no-commit-id",
                             "--diff-merges=first-parent", sha, "--"] + paths)
        return Parsers.parseDiff(r.text)
    }

    public func fileHistory(_ path: String, rev: String = "HEAD", limit: Int = 300) throws -> [Commit] {
        let r = try git("log", "--follow", "--decorate=full", "--format=\(Parsers.logFormat)",
                        "--max-count=\(limit)", rev, "--", path)
        return Parsers.parseLog(r.text)
    }

    public func blame(_ path: String, rev: String) throws -> [BlameLine] {
        Parsers.parseBlamePorcelain(try git("blame", "--porcelain", rev, "--", path).text)
    }

    // MARK: trees

    public func tree(_ rev: String) throws -> [TreeEntry] {
        Parsers.parseLsTree(try git("ls-tree", "-r", "-z", "--long", rev).text)
    }

    public func blob(_ rev: String, path: String) throws -> Data {
        try git("cat-file", "blob", "\(rev):\(path)").stdout
    }

    // MARK: working copy

    public func unstagedDiff(_ path: String, untracked: Bool = false) throws -> [FileDiff] {
        let r = try (untracked
            ? git("diff", "--no-index", "--", "/dev/null", path, okCodes: [0, 1])
            : git("diff", "--", path))
        return Parsers.parseDiff(r.text)
    }

    public func stagedDiff(_ path: String, oldPath: String? = nil) throws -> [FileDiff] {
        let paths = [oldPath, path].compactMap { $0 }
        return Parsers.parseDiff(try gitArgs(["diff", "--cached", "-M", "--"] + paths).text)
    }

    public func stage(_ paths: [String]) throws {
        guard !paths.isEmpty else { return }
        try gitArgs(["add", "-A", "--"] + paths)
    }

    public func unstage(_ paths: [String]) throws {
        guard !paths.isEmpty else { return }
        if headSHA() == nil {
            try gitArgs(["rm", "--cached", "-r", "-q", "--"] + paths)
        } else {
            try gitArgs(["restore", "--staged", "--"] + paths)
        }
    }

    /// Throw away work-tree changes; untracked files go to the Trash when possible.
    public func discard(tracked: [String], untracked: [String]) throws {
        if !tracked.isEmpty { try gitArgs(["restore", "--worktree", "--"] + tracked) }
        for p in untracked {
            let url = URL(fileURLWithPath: path).appendingPathComponent(p)
            #if os(macOS)
            if (try? FileManager.default.trashItem(at: url, resultingItemURL: nil)) != nil { continue }
            #endif
            try FileManager.default.removeItem(at: url)
        }
    }

    public func applyPatch(_ patch: String, cached: Bool, reverse: Bool) throws {
        var args = ["apply", "--whitespace=nowarn", "--recount"]
        if cached { args.append("--cached") }
        if reverse { args.append("--reverse") }
        args.append("-")
        try gitArgs(args, input: Data(patch.utf8))
    }

    public func stageHunk(_ fd: FileDiff, _ i: Int) throws { try applyPatch(fd.patch(forHunk: i), cached: true, reverse: false) }
    public func unstageHunk(_ fd: FileDiff, _ i: Int) throws { try applyPatch(fd.patch(forHunk: i), cached: true, reverse: true) }
    public func discardHunk(_ fd: FileDiff, _ i: Int) throws { try applyPatch(fd.patch(forHunk: i), cached: false, reverse: true) }

    @discardableResult
    public func commit(message: String, amend: Bool = false) throws -> String {
        var args = ["commit", "-q", "--cleanup=strip", "-F", "-"]
        if amend { args.append("--amend") }
        try gitArgs(args, input: Data(message.utf8))
        return headSHA() ?? ""
    }

    public func lastCommitMessage() -> String {
        guard let r = try? git("log", "-1", "--format=%B", check: false), r.code == 0 else { return "" }
        return r.text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public func identity() -> String {
        let name = (try? git("config", "user.name", check: false))?.text
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let email = (try? git("config", "user.email", check: false))?.text
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return name.isEmpty ? "" : "\(name) <\(email)>"
    }

    // MARK: branches

    public func branches() throws -> [Branch] {
        Parsers.parseBranches(try git("for-each-ref", "--format=\(Parsers.branchFormat)",
                                      "--sort=-committerdate", "refs/heads").text)
    }

    public func switchTo(_ name: String) throws { try git("switch", "--no-guess", name) }

    public func createBranch(_ name: String, start: String? = nil) throws {
        try git("check-ref-format", "--branch", name)
        try gitArgs(["switch", "-c", name] + (start.map { [$0] } ?? []))
    }

    public func deleteBranch(_ name: String, force: Bool = false) throws {
        try git("branch", force ? "-D" : "-d", name)
    }

    // MARK: remotes

    public func remotes() throws -> [Remote] { Parsers.parseRemotes(try git("remote", "-v").text) }

    public func remoteStates(branch: String?, remotes: [Remote]? = nil) throws -> [RemoteBranchState] {
        let rs = try remotes ?? self.remotes()
        return rs.map { r in
            guard let branch else { return RemoteBranchState(remote: r.name, exists: false) }
            let ref = "refs/remotes/\(r.name)/\(branch)"
            guard (try? git("rev-parse", "--verify", "-q", ref, check: false))?.code == 0 else {
                return RemoteBranchState(remote: r.name, exists: false)
            }
            guard let res = try? git("rev-list", "--left-right", "--count", "refs/heads/\(branch)...\(ref)",
                                     check: false), res.code == 0 else {
                return RemoteBranchState(remote: r.name, exists: true)
            }
            let parts = res.text.split(whereSeparator: { $0 == "\t" || $0 == " " || $0 == "\n" })
            let ahead = parts.count == 2 ? Int(parts[0]) ?? 0 : 0
            let behind = parts.count == 2 ? Int(parts[1]) ?? 0 : 0
            return RemoteBranchState(remote: r.name, exists: true, ahead: ahead, behind: behind)
        }
    }

    public func unpushedAnywhere() -> Int {
        guard headSHA() != nil,
              let r = try? git("rev-list", "--count", "HEAD", "--not", "--remotes", check: false),
              r.code == 0 else { return 0 }
        return Int(r.text.trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
    }

    public func fetch(_ remote: String, prune: Bool = false) throws {
        var args = ["-c", "maintenance.auto=false", "-c", "gc.auto=0",
                    "fetch", "--recurse-submodules=no", "--no-write-fetch-head"]
        if prune { args.append("--prune") }
        args.append(remote)
        try gitArgs(args, network: true)
    }

    public func pull(_ remote: String, branch: String, mode: PullMode = .ffOnly) throws {
        try gitArgs(["-c", "maintenance.auto=false", "-c", "gc.auto=0",
                     "pull", "--recurse-submodules=no", mode.rawValue, remote, branch], network: true)
    }

    public func push(_ remote: String, branch: String, setUpstream: Bool = false) throws {
        var args = ["push", "--recurse-submodules=no", "--porcelain"]
        if setUpstream { args.append("--set-upstream") }
        args += [remote, "refs/heads/\(branch):refs/heads/\(branch)"]
        try gitArgs(args, network: true)
    }

    public func lsRemoteCheck(_ remote: String) throws {
        try runner.run(["ls-remote", "--heads", remote], cwd: path, network: true, timeout: 30)
    }
}

func looksLikeHash(_ s: String) -> Bool {
    (4...64).contains(s.count) && s.allSatisfy { $0.isHexDigit }
}
