import Foundation
#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

/// Census-style health checks (in the spirit of qdvc-repo-census): read-only
/// apart from `git fetch`, one reachability check per server, and servers that
/// don't answer are skipped and shown as offline.
public struct ServerStatus: Hashable, Sendable, Identifiable {
    public var id: String { key }
    public let key: String          // host:port, or "local:<path>"
    public let host: String
    public let port: Int?
    public var online: Bool?
    public var remotes: [String] = []
    public var error: String?

    public init(key: String, host: String, port: Int?, online: Bool? = nil) {
        self.key = key
        self.host = host
        self.port = port
        self.online = online
    }

    public var isLocal: Bool { key.hasPrefix("local:") }
    public var displayName: String { isLocal ? host : port.map { "\(host):\($0)" } ?? host }
}

public enum Census {
    public static func summarize(path: String, runner: GitRunner, fetchErrors: [String: String] = [:],
                                 offline: [String] = []) -> RepoSummary {
        let name = (path as NSString).lastPathComponent
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: path, isDirectory: &isDir), isDir.boolValue else {
            return RepoSummary(path: path, name: name, level: .missing, reasons: ["Folder not found"])
        }
        let repo = Repository(runner: runner, path: path)
        let status: WorkingStatus
        do { status = try repo.status() } catch {
            return RepoSummary(path: path, name: name, level: .missing,
                               reasons: ["Not a Git repository (\(error.localizedDescription))"])
        }
        var s = RepoSummary(path: path, name: name, level: .ok)
        s.branch = status.branch.head
        s.detached = status.branch.head == nil && status.branch.oid != nil
        s.upstream = status.branch.upstream
        s.operation = repo.operationInProgress()
        for e in status.entries {
            if e.conflicted { s.conflicted += 1 }
            else if e.untracked { s.untracked += 1 }
            else {
                if e.index != "." { s.staged += 1 }
                if e.worktree != "." { s.modified += 1 }
            }
        }
        s.stashes = repo.stashCount()
        let remotes = (try? repo.remotes()) ?? []
        s.remotes = (try? repo.remoteStates(branch: s.branch, remotes: remotes)) ?? []
        s.unpushedAnywhere = remotes.isEmpty ? 0 : repo.unpushedAnywhere()
        s.fetchErrors = fetchErrors
        s.offlineRemotes = offline
        if let last = try? repo.git("log", "-1", "--format=%ct%x1f%s", check: false), last.code == 0 {
            let parts = Parsers.split(last.text.trimmingCharacters(in: .whitespacesAndNewlines), on: "\u{1F}")
            if parts.count == 2 {
                s.lastCommitTime = Int(parts[0])
                s.lastCommitSubject = parts[1]
            }
        }
        classify(&s, hasRemotes: !remotes.isEmpty)
        return s
    }

    public static func classify(_ s: inout RepoSummary, hasRemotes: Bool) {
        var r: [String] = []
        func plural(_ n: Int, _ w: String, _ p: String? = nil) -> String { "\(n) \(n == 1 ? w : (p ?? w + "s"))" }
        if let op = s.operation { r.append("\(op.capitalized) in progress") }
        if s.detached { r.append("Detached HEAD") }
        if s.conflicted > 0 { r.append("\(s.conflicted) conflicted") }
        if s.staged > 0 { r.append("\(s.staged) staged") }
        if s.modified > 0 { r.append("\(s.modified) modified") }
        if s.untracked > 0 { r.append("\(s.untracked) untracked") }
        if s.stashes > 0 { r.append(plural(s.stashes, "stash", "stashes")) }
        if hasRemotes && s.unpushedAnywhere > 0 { r.append(plural(s.unpushedAnywhere, "commit") + " on no remote") }
        for st in s.remotes where !s.offlineRemotes.contains(st.remote) && s.fetchErrors[st.remote] == nil {
            if !st.exists, let b = s.branch { r.append("\(st.remote) has no \(b)") }
            if st.ahead > 0 { r.append("\(st.ahead) to push to \(st.remote)") }
            if st.behind > 0 { r.append("\(st.behind) to pull from \(st.remote)") }
        }
        if hasRemotes && s.branch != nil && s.upstream == nil { r.append("No upstream branch") }
        s.reasons = r
        if !r.isEmpty { s.level = .attention }
        else if !s.offlineRemotes.isEmpty || !s.fetchErrors.isEmpty { s.level = .unknown }
        else { s.level = .ok }
    }

    /// Group remotes by server. Returns servers by key and (repoPath, remote) -> key.
    public static func groupServers(_ repos: [String: [Remote]], resolveSSH: Bool = true)
        -> ([String: ServerStatus], [String: String]) {
        var servers: [String: ServerStatus] = [:]
        var index: [String: String] = [:]
        var aliasCache: [String: (String, Int?)] = [:]
        for (repoPath, remotes) in repos {
            let rname = (repoPath as NSString).lastPathComponent
            for rem in remotes {
                let ep = Endpoint.parse(rem.fetchURL)
                let key: String
                if ep.isLocal {
                    key = "local:" + ep.path
                    if servers[key] == nil { servers[key] = ServerStatus(key: key, host: ep.path, port: nil) }
                } else {
                    var host = ep.host
                    var port = ep.effectivePort
                    if ep.scheme == "ssh" && resolveSSH {
                        if aliasCache[host] == nil { aliasCache[host] = resolveSSHAlias(host) }
                        let (real, realPort) = aliasCache[host]!
                        host = real
                        port = ep.port ?? realPort ?? port
                    }
                    key = "\(host.lowercased()):\(port.map(String.init) ?? "")"
                    if servers[key] == nil { servers[key] = ServerStatus(key: key, host: host, port: port) }
                }
                servers[key]!.remotes.append("\(rname)/\(rem.name)")
                index[indexKey(repoPath, rem.name)] = key
            }
        }
        return (servers, index)
    }

    public static func indexKey(_ repo: String, _ remote: String) -> String { repo + "\u{0}" + remote }

    /// Online = the port accepts a TCP connection (local remotes: the path exists).
    public static func probe(_ server: ServerStatus, timeout: TimeInterval = 4) -> ServerStatus {
        var s = server
        if s.isLocal {
            let path = (s.host as NSString).expandingTildeInPath
            s.online = FileManager.default.fileExists(atPath: path)
            if s.online == false { s.error = "Path not found" }
            return s
        }
        let (ok, err) = tcpConnect(host: s.host, port: s.port ?? 443, timeout: timeout)
        s.online = ok
        s.error = err
        return s
    }

    public static func probeAll(_ servers: [String: ServerStatus]) -> [String: ServerStatus] {
        final class Results: @unchecked Sendable {
            var values: [String: ServerStatus]
            let lock = NSLock()
            init(_ v: [String: ServerStatus]) { values = v }
        }
        let results = Results(servers)
        let keys = Array(servers.keys)
        DispatchQueue.concurrentPerform(iterations: keys.count) { i in
            let r = probe(servers[keys[i]]!)
            results.lock.lock()
            results.values[keys[i]] = r
            results.lock.unlock()
        }
        return results.values
    }

    static func tcpConnect(host: String, port: Int, timeout: TimeInterval) -> (Bool, String?) {
        var hints = addrinfo()
        hints.ai_family = AF_UNSPEC
        #if canImport(Darwin)
        hints.ai_socktype = SOCK_STREAM
        #else
        hints.ai_socktype = Int32(SOCK_STREAM.rawValue)
        #endif
        var res: UnsafeMutablePointer<addrinfo>?
        let rc = getaddrinfo(host, String(port), &hints, &res)
        guard rc == 0, let first = res else {
            return (false, "Name not resolved")
        }
        defer { freeaddrinfo(first) }
        var lastError = "No answer"
        var ai: UnsafeMutablePointer<addrinfo>? = first
        while let a = ai {
            let fd = socket(a.pointee.ai_family, a.pointee.ai_socktype, a.pointee.ai_protocol)
            if fd >= 0 {
                let flags = fcntl(fd, F_GETFL, 0)
                _ = fcntl(fd, F_SETFL, flags | O_NONBLOCK)
                let c = connect(fd, a.pointee.ai_addr, a.pointee.ai_addrlen)
                if c == 0 {
                    close(fd)
                    return (true, nil)
                }
                if errno == EINPROGRESS {
                    var pfd = pollfd(fd: fd, events: Int16(POLLOUT), revents: 0)
                    let pr = poll(&pfd, 1, Int32(timeout * 1000))
                    if pr > 0 {
                        var soErr: Int32 = 0
                        var len = socklen_t(MemoryLayout<Int32>.size)
                        getsockopt(fd, SOL_SOCKET, SO_ERROR, &soErr, &len)
                        close(fd)
                        if soErr == 0 { return (true, nil) }
                        lastError = String(cString: strerror(soErr))
                    } else {
                        close(fd)
                        lastError = pr == 0 ? "Timed out" : String(cString: strerror(errno))
                    }
                } else {
                    lastError = String(cString: strerror(errno))
                    close(fd)
                }
            }
            ai = a.pointee.ai_next
        }
        return (false, lastError)
    }
}
