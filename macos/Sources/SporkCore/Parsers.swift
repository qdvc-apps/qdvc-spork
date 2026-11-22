import Foundation

// Parsers for git's machine-readable output. Everything is split on Unicode
// scalars with `Parsers.split`, never on Characters: Swift treats "\r\n" as one
// Character, so `split(separator: "\n")` would leave CRLF files as one giant
// line.

public enum Parsers {
    public static let US = "\u{1F}"   // between fields
    public static let RS = "\u{1E}"   // between commits

    /// Split on a single Unicode scalar, keeping empty pieces.
    public static func split(_ s: String, on sep: Unicode.Scalar) -> [String] {
        s.unicodeScalars.split(separator: sep, omittingEmptySubsequences: false)
            .map { String(String.UnicodeScalarView($0)) }
    }

    public static let logFormat = ["%H", "%P", "%an", "%ae", "%at", "%cn", "%ce", "%ct", "%D", "%s"]
        .joined(separator: "%x1f") + "%x1e"
    public static let detailFormat = ["%H", "%P", "%an", "%ae", "%at", "%cn", "%ce", "%ct", "%D", "%s", "%b"]
        .joined(separator: "%x1f") + "%x1e"
    public static let branchFormat =
        "%(refname:short)%00%(objectname)%00%(upstream:short)%00%(HEAD)%00%(upstream:track)"

    // MARK: log

    public static func parseLog(_ text: String, withBody: Bool = false) -> [Commit] {
        var commits: [Commit] = []
        for rawRecord in split(text, on: "\u{1E}") {
            let record = rawRecord.drop(while: { $0 == "\n" })
            if record.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
            let f = split(String(record), on: "\u{1F}")
            if f.count < 10 { continue }
            let body = withBody && f.count > 10 ? f[10].trimmingCharacters(in: .whitespacesAndNewlines) : ""
            commits.append(Commit(
                sha: f[0],
                parents: f[1].split(separator: " ").map(String.init),
                authorName: f[2], authorEmail: f[3], authorTime: Int(f[4]) ?? 0,
                committerName: f[5], committerEmail: f[6], committerTime: Int(f[7]) ?? 0,
                refs: split(f[8], on: ",")
                    .map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty },
                subject: f[9], body: body))
        }
        return commits
    }

    /// `%D` entries produced with `--decorate=full`.
    public static func parseRefs(_ decorations: [String]) -> [RefLabel] {
        var out: [RefLabel] = []
        for d in decorations {
            if d.hasPrefix("HEAD -> ") {
                out.append(RefLabel(name: stripRef(String(d.dropFirst(8))), kind: .head))
            } else if d == "HEAD" {
                out.append(RefLabel(name: "HEAD", kind: .detached))
            } else if d.hasPrefix("tag: ") {
                out.append(RefLabel(name: stripRef(String(d.dropFirst(5))), kind: .tag))
            } else if d.hasPrefix("refs/remotes/") {
                let name = String(d.dropFirst("refs/remotes/".count))
                if name.hasSuffix("/HEAD") { continue }
                out.append(RefLabel(name: name, kind: .remote))
            } else if d.hasPrefix("refs/heads/") {
                out.append(RefLabel(name: String(d.dropFirst("refs/heads/".count)), kind: .branch))
            } else if d.hasPrefix("refs/stash") {
                continue
            } else {
                out.append(RefLabel(name: stripRef(d), kind: .branch))
            }
        }
        return out
    }

    static func stripRef(_ name: String) -> String {
        for p in ["refs/heads/", "refs/tags/", "refs/remotes/"] where name.hasPrefix(p) {
            return String(name.dropFirst(p.count))
        }
        return name
    }

    // MARK: status

    /// `git status --porcelain=v2 --branch -z`
    public static func parseStatusV2(_ data: String) -> WorkingStatus {
        var header = BranchHeader()
        var entries: [StatusEntry] = []
        let tokens = split(data, on: "\0")
        var i = 0
        while i < tokens.count {
            let tok = tokens[i]
            i += 1
            if tok.isEmpty { continue }
            if tok.hasPrefix("# ") {
                let rest = tok.dropFirst(2)
                let key = rest.prefix { $0 != " " }
                let value = rest.dropFirst(key.count).trimmingCharacters(in: .whitespaces)
                switch key {
                case "branch.oid": header.oid = value == "(initial)" ? nil : value
                case "branch.head": header.head = value == "(detached)" ? nil : value
                case "branch.upstream": header.upstream = value
                case "branch.ab":
                    let parts = value.split(separator: " ")
                    if parts.count == 2 {
                        header.ahead = Int(parts[0].dropFirst()) ?? 0
                        header.behind = Int(parts[1].dropFirst()) ?? 0
                    }
                default: break
                }
                continue
            }
            guard let kind = tok.first else { continue }
            switch kind {
            case "1":
                let parts = splitFields(tok, count: 9)
                guard parts.count == 9 else { continue }
                let xy = Array(parts[1])
                entries.append(StatusEntry(path: parts[8], index: String(xy[0]), worktree: String(xy[1])))
            case "2":
                let parts = splitFields(tok, count: 10)
                guard parts.count == 10 else { continue }
                let xy = Array(parts[1])
                let orig = i < tokens.count ? tokens[i] : nil
                i += 1
                entries.append(StatusEntry(path: parts[9], index: String(xy[0]),
                                           worktree: String(xy[1]), oldPath: orig))
            case "u":
                let parts = splitFields(tok, count: 11)
                guard parts.count == 11 else { continue }
                let xy = Array(parts[1])
                entries.append(StatusEntry(path: parts[10], index: String(xy[0]),
                                           worktree: String(xy[1]), conflicted: true))
            case "?":
                entries.append(StatusEntry(path: String(tok.dropFirst(2)), index: "?",
                                           worktree: "?", untracked: true))
            default:
                break
            }
        }
        return WorkingStatus(branch: header, entries: entries)
    }

    /// Split on spaces into exactly `count` fields; the last keeps any spaces (paths).
    static func splitFields(_ s: String, count: Int) -> [String] {
        var out: [String] = []
        var rest = Substring(s)
        while out.count < count - 1, let sp = rest.firstIndex(of: " ") {
            out.append(String(rest[..<sp]))
            rest = rest[rest.index(after: sp)...]
        }
        out.append(String(rest))
        return out
    }

    // MARK: changed files

    public static func parseNameStatus(_ data: String) -> [FileChange] {
        var out: [FileChange] = []
        let t = split(data, on: "\0")
        var i = 0
        while i < t.count {
            let code = t[i]
            i += 1
            guard let letter = code.first else { continue }
            if letter == "R" || letter == "C" {
                guard i + 1 < t.count else { break }
                out.append(FileChange(path: t[i + 1], status: String(letter), oldPath: t[i]))
                i += 2
            } else {
                guard i < t.count else { break }
                out.append(FileChange(path: t[i], status: String(letter)))
                i += 1
            }
        }
        return out
    }

    public static func parseNumstat(_ data: String) -> [String: (Int?, Int?)] {
        var out: [String: (Int?, Int?)] = [:]
        let t = split(data, on: "\0")
        var i = 0
        while i < t.count {
            let tok = t[i]
            i += 1
            let parts = split(tok, on: "\t")
            if parts.count < 3 { continue }
            let add = parts[0] == "-" ? nil : Int(parts[0])
            let del = parts[1] == "-" ? nil : Int(parts[1])
            if parts[2].isEmpty {
                let new = i + 1 < t.count ? t[i + 1] : ""
                i += 2
                out[new] = (add, del)
            } else {
                out[parts[2]] = (add, del)
            }
        }
        return out
    }

    public static func mergeNumstat(_ changes: [FileChange], _ stats: [String: (Int?, Int?)]) -> [FileChange] {
        changes.map { c in
            var c = c
            if let s = stats[c.path] {
                c.additions = s.0
                c.deletions = s.1
            }
            return c
        }
    }

    // MARK: unified diff

    public static func parseDiff(_ text: String) -> [FileDiff] {
        var files: [FileDiff] = []
        var inHunk = false
        var oldNo = 0
        var newNo = 0
        for raw in split(text, on: "\n") {
            if raw.hasPrefix("diff --git ") || raw.hasPrefix("diff --cc ") {
                files.append(FileDiff(headerLines: [raw]))
                inHunk = false
                continue
            }
            guard !files.isEmpty else { continue }
            let fi = files.count - 1
            if let h = parseHunkHeader(raw) {
                files[fi].hunks.append(h)
                oldNo = h.oldStart
                newNo = h.newStart
                inHunk = true
                continue
            }
            if !inHunk {
                if raw.hasPrefix("Binary files ") || raw.hasPrefix("GIT binary patch") {
                    files[fi].binary = true
                }
                if raw.hasPrefix("--- ") { files[fi].oldPath = diffPath(String(raw.dropFirst(4))) }
                if raw.hasPrefix("+++ ") { files[fi].newPath = diffPath(String(raw.dropFirst(4))) }
                if !raw.isEmpty { files[fi].headerLines.append(raw) }
                continue
            }
            guard let tag = raw.first else { continue }
            let body = String(raw.dropFirst())
            let hi = files[fi].hunks.count - 1
            switch tag {
            case " ":
                files[fi].hunks[hi].lines.append(DiffLine(kind: " ", text: body, oldNo: oldNo, newNo: newNo))
                oldNo += 1
                newNo += 1
            case "-":
                files[fi].hunks[hi].lines.append(DiffLine(kind: "-", text: body, oldNo: oldNo, newNo: nil))
                oldNo += 1
            case "+":
                files[fi].hunks[hi].lines.append(DiffLine(kind: "+", text: body, oldNo: nil, newNo: newNo))
                newNo += 1
            case "\\":
                files[fi].hunks[hi].lines.append(DiffLine(kind: "\\", text: body, oldNo: nil, newNo: nil))
            default:
                break
            }
        }
        for i in files.indices where files[i].oldPath == nil && files[i].newPath == nil {
            let (a, b) = pathsFromGitHeader(files[i].headerLines[0])
            files[i].oldPath = a
            files[i].newPath = b
        }
        return files
    }

    static func parseHunkHeader(_ raw: String) -> Hunk? {
        guard raw.hasPrefix("@@ -") else { return nil }
        // @@ -a[,b] +c[,d] @@ context
        let inner = raw.dropFirst(4)
        guard let end = inner.range(of: " @@") else { return nil }
        let spec = inner[..<end.lowerBound]          // "a,b +c,d"
        let halves = spec.split(separator: " ")
        guard halves.count == 2, halves[1].hasPrefix("+") else { return nil }
        func pair(_ s: Substring) -> (Int, Int)? {
            let p = s.split(separator: ",", omittingEmptySubsequences: false)
            guard let a = Int(p[0]) else { return nil }
            let b = p.count > 1 ? (Int(p[1]) ?? 1) : 1
            return (a, b)
        }
        guard let o = pair(halves[0]), let n = pair(halves[1].dropFirst()) else { return nil }
        return Hunk(header: raw, oldStart: o.0, oldCount: o.1, newStart: n.0, newCount: n.1)
    }

    static func diffPath(_ p: String) -> String? {
        var p = split(p, on: "\t")[0]
        if p == "/dev/null" { return nil }
        if p.hasPrefix("\"") && p.hasSuffix("\"") && p.count >= 2 {
            p = unquoteC(String(p.dropFirst().dropLast()))
        }
        if p.hasPrefix("a/") || p.hasPrefix("b/") { return String(p.dropFirst(2)) }
        return p
    }

    static func pathsFromGitHeader(_ line: String) -> (String?, String?) {
        guard line.hasPrefix("diff --git a/"), let r = line.range(of: " b/", options: .backwards) else {
            return (nil, nil)
        }
        let a = String(line[line.index(line.startIndex, offsetBy: 13)..<r.lowerBound])
        let b = String(line[r.upperBound...])
        return (a, b)
    }

    /// Undo git's C-style quoting of unusual paths.
    public static func unquoteC(_ s: String) -> String {
        let bytes = Array(s.utf8)
        var out: [UInt8] = []
        var i = 0
        let simple: [UInt8: UInt8] = [UInt8(ascii: "n"): 10, UInt8(ascii: "t"): 9, UInt8(ascii: "\""): 34,
                                      UInt8(ascii: "\\"): 92, UInt8(ascii: "a"): 7, UInt8(ascii: "b"): 8,
                                      UInt8(ascii: "f"): 12, UInt8(ascii: "r"): 13, UInt8(ascii: "v"): 11]
        while i < bytes.count {
            let c = bytes[i]
            if c == 92, i + 1 < bytes.count {
                let n = bytes[i + 1]
                if let v = simple[n] {
                    out.append(v)
                    i += 2
                    continue
                }
                if (48...55).contains(n), i + 3 < bytes.count + 1,
                   let v = UInt8(String(decoding: bytes[(i + 1)..<min(i + 4, bytes.count)], as: UTF8.self), radix: 8) {
                    out.append(v)
                    i += 4
                    continue
                }
            }
            out.append(c)
            i += 1
        }
        return String(decoding: out, as: UTF8.self)
    }

    // MARK: refs, remotes, trees, blame

    public static func parseBranches(_ text: String) -> [Branch] {
        split(text, on: "\n").compactMap { line in
            let f = split(line, on: "\0")
            guard f.count >= 5, !f[0].isEmpty else { return nil }
            return Branch(name: f[0], sha: f[1], upstream: f[2].isEmpty ? nil : f[2],
                          isHead: f[3] == "*", track: f[4])
        }
    }

    /// `git remote -v`
    public static func parseRemotes(_ text: String) -> [Remote] {
        var order: [String] = []
        var fetch: [String: String] = [:]
        var push: [String: String] = [:]
        for line in split(text, on: "\n") {
            guard let tab = line.firstIndex(where: { $0 == "\t" || $0 == " " }) else { continue }
            let name = String(line[..<tab])
            var rest = line[line.index(after: tab)...].trimmingCharacters(in: .whitespaces)
            var kind = ""
            if rest.hasSuffix(" (fetch)") { kind = "fetch"; rest = String(rest.dropLast(8)) }
            else if rest.hasSuffix(" (push)") { kind = "push"; rest = String(rest.dropLast(7)) }
            else { continue }
            if !order.contains(name) { order.append(name) }
            if kind == "fetch" { fetch[name] = rest } else { push[name] = rest }
        }
        return order.map { n in
            Remote(name: n, fetchURL: fetch[n] ?? push[n] ?? "", pushURL: push[n] ?? fetch[n] ?? "")
        }
    }

    /// `git ls-tree -r -z --long`
    public static func parseLsTree(_ data: String) -> [TreeEntry] {
        split(data, on: "\0").compactMap { rec in
            guard let tab = rec.firstIndex(of: "\t") else { return nil }
            let meta = rec[..<tab].split(separator: " ", omittingEmptySubsequences: true)
            let path = String(rec[rec.index(after: tab)...])
            guard meta.count >= 3 else { return nil }
            let size = meta.count >= 4 && meta[3] != "-" ? Int(meta[3]) : nil
            return TreeEntry(path: path, mode: String(meta[0]), kind: String(meta[1]),
                             sha: String(meta[2]), size: size)
        }
    }

    public static func parseBlamePorcelain(_ text: String) -> [BlameLine] {
        var lines: [BlameLine] = []
        var info: [String: [String: String]] = [:]
        var cur: String?
        var finalNo = 0
        for raw in split(text, on: "\n") {
            if raw.hasPrefix("\t") {
                let meta = info[cur ?? ""] ?? [:]
                lines.append(BlameLine(sha: cur ?? "", author: meta["author"] ?? "",
                                       authorTime: Int(meta["author-time"] ?? "") ?? 0,
                                       summary: meta["summary"] ?? "", lineNo: finalNo,
                                       text: String(raw.dropFirst())))
                continue
            }
            let parts = raw.split(separator: " ")
            if parts.count >= 3, parts[0].count >= 40, parts[0].allSatisfy({ $0.isHexDigit }),
               let n = Int(parts[2]) {
                cur = String(parts[0])
                finalNo = n
                if info[cur!] == nil { info[cur!] = [:] }
                continue
            }
            if let c = cur, let sp = raw.firstIndex(of: " ") {
                let key = String(raw[..<sp])
                if info[c]?[key] == nil {
                    info[c]?[key] = String(raw[raw.index(after: sp)...])
                }
            }
        }
        return lines
    }

    public static func isProbablyBinary(_ data: Data) -> Bool {
        data.prefix(8000).contains(0)
    }
}
