import Foundation

// Named SporkSettings, not Settings: SwiftUI already has a `Settings` scene
// and the names would clash (Cursed Knowledge).

public struct Bookmark: Hashable, Sendable, Identifiable {
    public var id: String { path }
    public var path: String
    public var name: String?
    public init(path: String, name: String? = nil) {
        self.path = path
        self.name = name
    }
    public var displayName: String { name ?? (path as NSString).lastPathComponent }
}

public struct TrustedServerEntry: Hashable, Sendable, Identifiable {
    public var id: String { url }
    public var url: String
    public var certificate: String
    public var label: String?
    public init(url: String, certificate: String, label: String? = nil) {
        self.url = url
        self.certificate = certificate
        self.label = label
    }
}

/// The settings file, in the format shared with the GTK edition
/// (docs/SETTINGS_FORMAT.md). Unknown keys are kept when saving.
public struct SporkSettings: Sendable {
    public var bookmarks: [Bookmark] = []
    public var servers: [TrustedServerEntry] = []
    public var editor = ""          // app path or bundle id; "" = default app
    public var terminal = ""        // app path or bundle id; "" = Terminal
    public var fetchTimeout = 180
    public var showAllBranches = false
    public var fileURL: URL
    var extra: [String: AnySendable] = [:]

    public init(fileURL: URL) { self.fileURL = fileURL }

    public static var supportDirectory: URL {
        #if os(macOS)
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("QDVC Spork", isDirectory: true)
        #else
        let base = ProcessInfo.processInfo.environment["XDG_CONFIG_HOME"].map { URL(fileURLWithPath: $0) }
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".config")
        return base.appendingPathComponent("qdvc-spork", isDirectory: true)
        #endif
    }

    public static var defaultURL: URL { supportDirectory.appendingPathComponent("settings.json") }

    public static func load(from url: URL = SporkSettings.defaultURL) -> SporkSettings {
        var s = SporkSettings(fileURL: url)
        guard let data = try? Data(contentsOf: url) else { return s }
        guard let raw = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            try? FileManager.default.copyItem(at: url, to: url.appendingPathExtension("broken"))
            return s
        }
        if let bms = raw["bookmarks"] as? [[String: Any]] {
            s.bookmarks = bms.compactMap { b in
                guard let p = b["path"] as? String, !p.isEmpty else { return nil }
                return Bookmark(path: p, name: b["name"] as? String)
            }
        }
        if let srv = raw["servers"] as? [[String: Any]] {
            s.servers = srv.compactMap { v in
                guard let u = v["url"] as? String, let c = v["certificate"] as? String else { return nil }
                return TrustedServerEntry(url: u, certificate: c, label: v["label"] as? String)
            }
        }
        s.editor = raw["editor"] as? String ?? ""
        s.terminal = raw["terminal"] as? String ?? ""
        s.fetchTimeout = raw["fetchTimeout"] as? Int ?? 180
        s.showAllBranches = raw["showAllBranches"] as? Bool ?? false
        let known: Set<String> = ["version", "bookmarks", "servers", "editor", "terminal",
                                  "fetchTimeout", "showAllBranches"]
        for (k, v) in raw where !known.contains(k) { s.extra[k] = AnySendable(v) }
        return s
    }

    public func save() throws {
        var out: [String: Any] = extra.mapValues { $0.value }
        out["version"] = 1
        out["bookmarks"] = bookmarks.map { b -> [String: Any] in
            var d: [String: Any] = ["path": b.path]
            if let n = b.name { d["name"] = n }
            return d
        }
        out["servers"] = servers.map { v -> [String: Any] in
            var d: [String: Any] = ["url": v.url, "certificate": v.certificate]
            if let l = v.label { d["label"] = l }
            return d
        }
        out["editor"] = editor
        out["terminal"] = terminal
        out["fetchTimeout"] = fetchTimeout
        out["showAllBranches"] = showAllBranches
        let data = try JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted, .sortedKeys])
        try FileManager.default.createDirectory(at: fileURL.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try data.write(to: fileURL, options: .atomic)
    }

    // MARK: bookmarks

    @discardableResult
    public mutating func addBookmark(_ path: String) -> Bookmark {
        let real = (path as NSString).resolvingSymlinksInPath
        if let b = bookmarks.first(where: { ($0.path as NSString).resolvingSymlinksInPath == real }) { return b }
        let b = Bookmark(path: real)
        bookmarks.append(b)
        return b
    }

    public mutating func removeBookmark(_ path: String) { bookmarks.removeAll { $0.path == path } }

    public mutating func moveBookmark(_ path: String, by delta: Int) {
        guard let i = bookmarks.firstIndex(where: { $0.path == path }) else { return }
        let b = bookmarks.remove(at: i)
        bookmarks.insert(b, at: max(0, min(i + delta, bookmarks.count)))
    }

    // MARK: servers

    public var certsDirectory: URL { fileURL.deletingLastPathComponent().appendingPathComponent("certs") }

    /// Copy the certificate into Spork's folder and trust it for `url`.
    @discardableResult
    public mutating func addServer(url: String, certificateAt source: URL, label: String? = nil) throws -> TrustedServerEntry {
        let norm = Endpoint.normaliseServerURL(url)
        try FileManager.default.createDirectory(at: certsDirectory, withIntermediateDirectories: true)
        let dest = certsDirectory.appendingPathComponent(Certificates.fileName(for: norm))
        if source.standardizedFileURL != dest.standardizedFileURL {
            if FileManager.default.fileExists(atPath: dest.path) { try FileManager.default.removeItem(at: dest) }
            try FileManager.default.copyItem(at: source, to: dest)
        }
        servers.removeAll { $0.url == norm }
        let entry = TrustedServerEntry(url: norm, certificate: dest.path, label: label)
        servers.append(entry)
        return entry
    }

    public mutating func removeServer(_ url: String) {
        guard let v = servers.first(where: { $0.url == url }) else { return }
        servers.removeAll { $0.url == url }
        if !servers.contains(where: { $0.certificate == v.certificate }) {
            try? FileManager.default.removeItem(atPath: v.certificate)
        }
    }

    public var trustedServers: [TrustedServer] {
        servers.filter { FileManager.default.fileExists(atPath: $0.certificate) }
            .map { TrustedServer(url: $0.url, certPath: $0.certificate) }
    }
}

/// Wraps JSON values we don't understand so they survive a save.
public struct AnySendable: @unchecked Sendable {
    public let value: Any
    public init(_ v: Any) { value = v }
}
