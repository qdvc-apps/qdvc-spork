import Foundation

// Plain value types shared by the core and the app. Every public struct has an
// explicit public init: Swift's memberwise initialisers are internal even for
// public structs (Cursed Knowledge).

public let workingCopyID = "WORKING"

public struct Commit: Identifiable, Hashable, Sendable {
    public var id: String { sha }
    public let sha: String
    public let parents: [String]
    public let authorName: String
    public let authorEmail: String
    public let authorTime: Int
    public let committerName: String
    public let committerEmail: String
    public let committerTime: Int
    public let refs: [String]
    public let subject: String
    public var body: String

    public init(sha: String, parents: [String], authorName: String, authorEmail: String,
                authorTime: Int, committerName: String, committerEmail: String,
                committerTime: Int, refs: [String], subject: String, body: String = "") {
        self.sha = sha
        self.parents = parents
        self.authorName = authorName
        self.authorEmail = authorEmail
        self.authorTime = authorTime
        self.committerName = committerName
        self.committerEmail = committerEmail
        self.committerTime = committerTime
        self.refs = refs
        self.subject = subject
        self.body = body
    }

    public var shortSHA: String { String(sha.prefix(8)) }
    public var isMerge: Bool { parents.count > 1 }
    public var authorDate: Date { Date(timeIntervalSince1970: TimeInterval(authorTime)) }
    public var committerDate: Date { Date(timeIntervalSince1970: TimeInterval(committerTime)) }
}

public enum RefKind: String, Sendable {
    case head, branch, remote, tag, detached
}

public struct RefLabel: Hashable, Sendable {
    public let name: String
    public let kind: RefKind
    public init(name: String, kind: RefKind) {
        self.name = name
        self.kind = kind
    }
}

public struct FileChange: Identifiable, Hashable, Sendable {
    public var id: String { path }
    public let path: String
    public let status: String          // A, M, D, R, C, T, U, ?
    public let oldPath: String?
    public var additions: Int?         // nil for binary files
    public var deletions: Int?

    public init(path: String, status: String, oldPath: String? = nil,
                additions: Int? = nil, deletions: Int? = nil) {
        self.path = path
        self.status = status
        self.oldPath = oldPath
        self.additions = additions
        self.deletions = deletions
    }

    public var displayPath: String {
        if let old = oldPath, old != path { return "\(old) \u{2192} \(path)" }
        return path
    }
}

public struct StatusEntry: Identifiable, Hashable, Sendable {
    public var id: String { path }
    public let path: String
    public let index: String       // X ("." = unchanged)
    public let worktree: String    // Y
    public let oldPath: String?
    public let conflicted: Bool
    public let untracked: Bool

    public init(path: String, index: String, worktree: String, oldPath: String? = nil,
                conflicted: Bool = false, untracked: Bool = false) {
        self.path = path
        self.index = index
        self.worktree = worktree
        self.oldPath = oldPath
        self.conflicted = conflicted
        self.untracked = untracked
    }

    public var staged: Bool { !untracked && !conflicted && index != "." }
    public var unstaged: Bool { untracked || conflicted || worktree != "." }
}

public struct BranchHeader: Hashable, Sendable {
    public var oid: String?
    public var head: String?
    public var upstream: String?
    public var ahead: Int
    public var behind: Int

    public init(oid: String? = nil, head: String? = nil, upstream: String? = nil,
                ahead: Int = 0, behind: Int = 0) {
        self.oid = oid
        self.head = head
        self.upstream = upstream
        self.ahead = ahead
        self.behind = behind
    }
}

public struct WorkingStatus: Hashable, Sendable {
    public var branch: BranchHeader
    public var entries: [StatusEntry]

    public init(branch: BranchHeader, entries: [StatusEntry]) {
        self.branch = branch
        self.entries = entries
    }

    public var staged: [StatusEntry] { entries.filter { $0.staged } }
    public var unstaged: [StatusEntry] { entries.filter { $0.unstaged } }
    public var conflicted: [StatusEntry] { entries.filter { $0.conflicted } }
    public var untracked: [StatusEntry] { entries.filter { $0.untracked } }
    public var isClean: Bool { entries.isEmpty }
}

public struct Remote: Identifiable, Hashable, Sendable {
    public var id: String { name }
    public let name: String
    public let fetchURL: String
    public let pushURL: String

    public init(name: String, fetchURL: String, pushURL: String) {
        self.name = name
        self.fetchURL = fetchURL
        self.pushURL = pushURL
    }
}

public struct RemoteBranchState: Identifiable, Hashable, Sendable {
    public var id: String { remote }
    public let remote: String
    public let exists: Bool
    public let ahead: Int
    public let behind: Int

    public init(remote: String, exists: Bool, ahead: Int = 0, behind: Int = 0) {
        self.remote = remote
        self.exists = exists
        self.ahead = ahead
        self.behind = behind
    }
}

public struct Branch: Identifiable, Hashable, Sendable {
    public var id: String { name }
    public let name: String
    public let sha: String
    public let upstream: String?
    public let isHead: Bool
    public let track: String

    public init(name: String, sha: String, upstream: String?, isHead: Bool, track: String = "") {
        self.name = name
        self.sha = sha
        self.upstream = upstream
        self.isHead = isHead
        self.track = track
    }
}

public struct TreeEntry: Hashable, Sendable {
    public let path: String
    public let mode: String
    public let kind: String     // blob, tree, commit (submodule)
    public let sha: String
    public let size: Int?

    public init(path: String, mode: String, kind: String, sha: String, size: Int?) {
        self.path = path
        self.mode = mode
        self.kind = kind
        self.sha = sha
        self.size = size
    }
}

public struct BlameLine: Hashable, Sendable, Identifiable {
    public var id: Int { lineNo }
    public let sha: String
    public let author: String
    public let authorTime: Int
    public let summary: String
    public let lineNo: Int
    public let text: String

    public init(sha: String, author: String, authorTime: Int, summary: String,
                lineNo: Int, text: String) {
        self.sha = sha
        self.author = author
        self.authorTime = authorTime
        self.summary = summary
        self.lineNo = lineNo
        self.text = text
    }
}

public struct DiffLine: Hashable, Sendable {
    public let kind: Character      // " ", "+", "-", "\\"
    public let text: String
    public let oldNo: Int?
    public let newNo: Int?

    public init(kind: Character, text: String, oldNo: Int?, newNo: Int?) {
        self.kind = kind
        self.text = text
        self.oldNo = oldNo
        self.newNo = newNo
    }
}

public struct Hunk: Hashable, Sendable {
    public let header: String
    public let oldStart: Int
    public let oldCount: Int
    public let newStart: Int
    public let newCount: Int
    public var lines: [DiffLine]

    public init(header: String, oldStart: Int, oldCount: Int, newStart: Int, newCount: Int,
                lines: [DiffLine] = []) {
        self.header = header
        self.oldStart = oldStart
        self.oldCount = oldCount
        self.newStart = newStart
        self.newCount = newCount
        self.lines = lines
    }

    public var patchText: String {
        var out = [header]
        for l in lines { out.append(String(l.kind) + l.text) }
        return out.joined(separator: "\n") + "\n"
    }
}

public struct FileDiff: Hashable, Sendable {
    public var headerLines: [String]
    public var oldPath: String?
    public var newPath: String?
    public var hunks: [Hunk]
    public var binary: Bool

    public init(headerLines: [String], oldPath: String? = nil, newPath: String? = nil,
                hunks: [Hunk] = [], binary: Bool = false) {
        self.headerLines = headerLines
        self.oldPath = oldPath
        self.newPath = newPath
        self.hunks = hunks
        self.binary = binary
    }

    /// A patch with just one hunk, suitable for `git apply`.
    public func patch(forHunk index: Int) -> String {
        headerLines.joined(separator: "\n") + "\n" + hunks[index].patchText
    }

    public var additions: Int { hunks.reduce(0) { $0 + $1.lines.filter { $0.kind == "+" }.count } }
    public var deletions: Int { hunks.reduce(0) { $0 + $1.lines.filter { $0.kind == "-" }.count } }
}

/// Census-style verdict for a repository.
public enum Level: String, Sendable, CaseIterable {
    case ok, attention, unknown, missing
}

public struct RepoSummary: Hashable, Sendable {
    public var path: String
    public var name: String
    public var level: Level
    public var branch: String?
    public var detached = false
    public var operation: String?
    public var staged = 0
    public var modified = 0
    public var untracked = 0
    public var conflicted = 0
    public var stashes = 0
    public var unpushedAnywhere = 0
    public var remotes: [RemoteBranchState] = []
    public var upstream: String?
    public var lastCommitTime: Int?
    public var lastCommitSubject: String?
    public var fetchErrors: [String: String] = [:]
    public var offlineRemotes: [String] = []
    public var reasons: [String] = []

    public init(path: String, name: String, level: Level, reasons: [String] = []) {
        self.path = path
        self.name = name
        self.level = level
        self.reasons = reasons
    }

    public var totalAhead: Int { remotes.filter { $0.exists }.map { $0.ahead }.max() ?? 0 }
    public var totalBehind: Int { remotes.filter { $0.exists }.map { $0.behind }.max() ?? 0 }
    public var dirtyCount: Int { staged + modified + untracked + conflicted }
}
