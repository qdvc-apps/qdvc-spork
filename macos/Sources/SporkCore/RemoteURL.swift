import Foundation

/// Which server (scheme, host, port) a remote URL talks to.
public struct Endpoint: Hashable, Sendable {
    public let scheme: String      // https, http, ssh, git, file
    public let host: String
    public let port: Int?
    public let user: String?
    public let path: String

    public init(scheme: String, host: String, port: Int?, user: String?, path: String) {
        self.scheme = scheme
        self.host = host
        self.port = port
        self.user = user
        self.path = path
    }

    public static let defaultPorts = ["https": 443, "http": 80, "ssh": 22, "git": 9418]

    public var isLocal: Bool { scheme == "file" }
    public var effectivePort: Int? { port ?? Endpoint.defaultPorts[scheme] }
    public var serverKey: String { isLocal ? "local" : "\(host.lowercased()):\(effectivePort.map(String.init) ?? "")" }

    /// The `http.<url>` prefix git would match for this server, port included.
    public var httpsBase: String? {
        guard scheme == "https" || scheme == "http" else { return nil }
        return "\(scheme)://\(host.lowercased())\(port.map { ":\($0)" } ?? "")/"
    }

    public static func parse(_ raw: String) -> Endpoint {
        let url = raw.trimmingCharacters(in: .whitespaces)
        if url.contains("://") {
            guard let c = URLComponents(string: url) else {
                return Endpoint(scheme: "file", host: "", port: nil, user: nil, path: url)
            }
            var scheme = (c.scheme ?? "").lowercased()
            if scheme == "file" { return Endpoint(scheme: "file", host: "", port: nil, user: nil, path: c.path) }
            if scheme.hasPrefix("git+ssh") || scheme.hasPrefix("ssh+git") { scheme = "ssh" }
            return Endpoint(scheme: scheme, host: c.host ?? "", port: c.port, user: c.user, path: c.path)
        }
        if url.hasPrefix("/") || url.hasPrefix(".") || url.hasPrefix("~") {
            return Endpoint(scheme: "file", host: "", port: nil, user: nil, path: url)
        }
        // scp-like: [user@]host:path (the colon comes before any slash)
        if let colon = url.firstIndex(of: ":") {
            let slash = url.firstIndex(of: "/")
            if slash == nil || colon < slash! {
                let userHost = url[..<colon]
                let path = String(url[url.index(after: colon)...])
                if let at = userHost.lastIndex(of: "@") {
                    return Endpoint(scheme: "ssh", host: String(userHost[userHost.index(after: at)...]),
                                    port: nil, user: String(userHost[..<at]), path: path)
                }
                return Endpoint(scheme: "ssh", host: String(userHost), port: nil, user: nil, path: path)
            }
        }
        return Endpoint(scheme: "file", host: "", port: nil, user: nil, path: url)
    }

    /// `scheme://host[:port]/path/` — git matches `http.<url>.*` exactly on
    /// scheme, host and port, and by prefix on the path.
    public static func normaliseServerURL(_ raw: String) -> String {
        var s = raw.trimmingCharacters(in: .whitespaces)
        if !s.contains("://") { s = "https://" + s }
        guard let c = URLComponents(string: s) else { return s }
        let scheme = (c.scheme ?? "https").lowercased()
        let host = (c.host ?? "").lowercased()
        var path = c.path.isEmpty ? "/" : c.path
        if !path.hasSuffix("/") { path += "/" }
        return "\(scheme)://\(host)\(c.port.map { ":\($0)" } ?? "")\(path)"
    }
}

/// Resolve ~/.ssh/config aliases with `ssh -G host`.
public func resolveSSHAlias(_ host: String) -> (String, Int?) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
    if !FileManager.default.isExecutableFile(atPath: "/usr/bin/ssh") { return (host, nil) }
    p.arguments = ["-G", host]
    let out = Pipe()
    p.standardOutput = out
    p.standardError = FileHandle.nullDevice
    do { try p.run() } catch { return (host, nil) }
    let data = out.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    guard p.terminationStatus == 0 else { return (host, nil) }
    var hostname = host
    var port: Int?
    for line in Parsers.split(String(decoding: data, as: UTF8.self), on: "\n") {
        let parts = line.split(separator: " ", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { continue }
        if parts[0] == "hostname" { hostname = parts[1] }
        if parts[0] == "port" { port = Int(parts[1]) }
    }
    return (hostname, port)
}
