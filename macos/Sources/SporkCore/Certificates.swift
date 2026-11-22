import Foundation
#if canImport(CryptoKit)
import CryptoKit
#endif

/// What Spork shows about a server's certificate (Forgejo's cert.pem, say).
///
/// Spork doesn't use the macOS keychain or ~/.gitconfig for these: it keeps a
/// copy of the file and passes `-c http.<url>.sslCAInfo=<copy>` to git.
public struct CertInfo: Hashable, Sendable {
    public let subjectCN: String?
    public let dnsNames: [String]
    public let ipAddresses: [String]
    public let notBefore: Date
    public let notAfter: Date
    public let isCA: Bool
    public let sha256: String

    public var names: [String] {
        var out = dnsNames + ipAddresses
        if out.isEmpty, let cn = subjectCN { out.append(cn) }
        return out
    }

    public func daysLeft(now: Date = Date()) -> Int {
        Int(floor(notAfter.timeIntervalSince(now) / 86400))
    }

    public func covers(_ host: String) -> Bool {
        let h = host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
        for n in names.map({ $0.lowercased() }) {
            if n == h { return true }
            if n.hasPrefix("*."), let dot = h.firstIndex(of: "."), h[h.index(after: dot)...] == n.dropFirst(2) {
                return true
            }
        }
        return false
    }
}

public enum CertificateError: Error, LocalizedError {
    case privateKey, notFound, malformed(String)
    public var errorDescription: String? {
        switch self {
        case .privateKey: return "This is a private key (key.pem), not a certificate. Use cert.pem, and never copy key.pem off the server."
        case .notFound: return "No certificate found in this file."
        case .malformed(let why): return "This doesn't look like a certificate (\(why))."
        }
    }
}

public enum Certificates {
    static let oidCN: [UInt8] = [0x55, 0x04, 0x03]
    static let oidSAN: [UInt8] = [0x55, 0x1D, 0x11]
    static let oidBasicConstraints: [UInt8] = [0x55, 0x1D, 0x13]

    public static func load(_ data: Data) throws -> [CertInfo] {
        let text = String(data: data, encoding: .ascii) ?? ""
        if text.contains("-----BEGIN") {
            if text.contains("PRIVATE KEY-----") && !text.contains("CERTIFICATE-----") {
                throw CertificateError.privateKey
            }
            let blocks = pemBlocks(text)
            if blocks.isEmpty { throw CertificateError.notFound }
            return try blocks.map(parseDER)
        }
        return [try parseDER(data)]
    }

    static func pemBlocks(_ text: String) -> [Data] {
        var out: [Data] = []
        var rest = Substring(text)
        while let b = rest.range(of: "-----BEGIN CERTIFICATE-----"),
              let e = rest.range(of: "-----END CERTIFICATE-----", range: b.upperBound..<rest.endIndex) {
            let body = rest[b.upperBound..<e.lowerBound].filter { !$0.isWhitespace }
            if let d = Data(base64Encoded: String(body)) { out.append(d) }
            rest = rest[e.upperBound...]
        }
        return out
    }

    struct TLV {
        let tag: UInt8
        let start: Int
        let end: Int
    }

    static func readTLV(_ b: [UInt8], _ pos: Int) throws -> TLV {
        guard pos + 2 <= b.count else { throw CertificateError.malformed("truncated") }
        let tag = b[pos]
        var len = Int(b[pos + 1])
        var p = pos + 2
        if len & 0x80 != 0 {
            let n = len & 0x7F
            guard n > 0, n <= 4, p + n <= b.count else { throw CertificateError.malformed("length") }
            len = 0
            for k in 0..<n { len = (len << 8) | Int(b[p + k]) }
            p += n
        }
        guard p + len <= b.count else { throw CertificateError.malformed("truncated") }
        return TLV(tag: tag, start: p, end: p + len)
    }

    static func children(_ b: [UInt8], _ start: Int, _ end: Int) throws -> [TLV] {
        var out: [TLV] = []
        var p = start
        while p < end {
            let t = try readTLV(b, p)
            out.append(t)
            p = t.end
        }
        return out
    }

    static func parseTime(_ tag: UInt8, _ raw: [UInt8]) throws -> Date {
        var s = String(decoding: raw, as: UTF8.self)
        if s.hasSuffix("Z") { s.removeLast() }
        let chars = Array(s)
        func num(_ a: Int, _ n: Int) -> Int { Int(String(chars[a..<min(a + n, chars.count)])) ?? 0 }
        var year: Int
        var o: Int
        if tag == 0x17 {
            year = num(0, 2)
            year += year < 50 ? 2000 : 1900
            o = 2
        } else if tag == 0x18 {
            year = num(0, 4)
            o = 4
        } else {
            throw CertificateError.malformed("time")
        }
        var dc = DateComponents()
        dc.year = year
        dc.month = num(o, 2)
        dc.day = num(o + 2, 2)
        dc.hour = num(o + 4, 2)
        dc.minute = num(o + 6, 2)
        dc.second = chars.count >= o + 10 ? num(o + 8, 2) : 0
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        guard let d = cal.date(from: dc) else { throw CertificateError.malformed("date") }
        return d
    }

    public static func parseDER(_ der: Data) throws -> CertInfo {
        let b = [UInt8](der)
        let outer = try readTLV(b, 0)
        guard outer.tag == 0x30 else { throw CertificateError.malformed("not DER") }
        let top = try children(b, outer.start, outer.end)
        guard let tbsTLV = top.first, tbsTLV.tag == 0x30 else { throw CertificateError.malformed("no TBS") }
        let tbs = try children(b, tbsTLV.start, tbsTLV.end)
        var i = 0
        if tbs.first?.tag == 0xA0 { i = 1 }
        guard tbs.count >= i + 6 else { throw CertificateError.malformed("short TBS") }
        let validity = try children(b, tbs[i + 3].start, tbs[i + 3].end)
        guard validity.count >= 2 else { throw CertificateError.malformed("validity") }
        let notBefore = try parseTime(validity[0].tag, Array(b[validity[0].start..<validity[0].end]))
        let notAfter = try parseTime(validity[1].tag, Array(b[validity[1].start..<validity[1].end]))
        let cn = try findCN(b, tbs[i + 4])

        var dns: [String] = []
        var ips: [String] = []
        var isCA = false
        for ext in tbs.dropFirst(i + 6) where ext.tag == 0xA3 {
            guard let seq = try children(b, ext.start, ext.end).first else { continue }
            for x in try children(b, seq.start, seq.end) {
                let parts = try children(b, x.start, x.end)
                guard let oidTLV = parts.first, let value = parts.last else { continue }
                let oid = Array(b[oidTLV.start..<oidTLV.end])
                if oid == oidSAN, let inner = try children(b, value.start, value.end).first {
                    for g in try children(b, inner.start, inner.end) {
                        let bytes = Array(b[g.start..<g.end])
                        if g.tag == 0x82 {
                            dns.append(String(decoding: bytes, as: UTF8.self))
                        } else if g.tag == 0x87 {
                            if bytes.count == 4 {
                                ips.append(bytes.map(String.init).joined(separator: "."))
                            } else if bytes.count == 16 {
                                let groups = stride(from: 0, to: 16, by: 2).map {
                                    String(format: "%x", (Int(bytes[$0]) << 8) | Int(bytes[$0 + 1]))
                                }
                                ips.append(groups.joined(separator: ":"))
                            }
                        }
                    }
                } else if oid == oidBasicConstraints, let inner = try children(b, value.start, value.end).first {
                    let bc = try children(b, inner.start, inner.end)
                    if let flag = bc.first, flag.tag == 0x01, flag.end > flag.start {
                        isCA = b[flag.start] != 0
                    }
                }
            }
        }
        return CertInfo(subjectCN: cn, dnsNames: dns, ipAddresses: ips, notBefore: notBefore,
                        notAfter: notAfter, isCA: isCA, sha256: fingerprint(der))
    }

    static func findCN(_ b: [UInt8], _ name: TLV) throws -> String? {
        for set in try children(b, name.start, name.end) {
            for atv in try children(b, set.start, set.end) {
                let parts = try children(b, atv.start, atv.end)
                if parts.count >= 2, parts[0].tag == 0x06, Array(b[parts[0].start..<parts[0].end]) == oidCN {
                    return String(decoding: b[parts[1].start..<parts[1].end], as: UTF8.self)
                }
            }
        }
        return nil
    }

    static func fingerprint(_ der: Data) -> String {
        #if canImport(CryptoKit)
        let digest = SHA256.hash(data: der)
        return digest.map { String(format: "%02X", $0) }.joined(separator: ":")
        #else
        return ""
        #endif
    }

    // MARK: matching certificates to remotes

    public struct URLSuggestion: Hashable, Sendable {
        public let url: String
        public var remotes: [String]
    }

    /// `remoteURLs` maps a label such as "dotfiles: nas" to the remote's URL.
    public static func suggestServerURLs(_ cert: CertInfo, remoteURLs: [String: String]) -> [URLSuggestion] {
        var found: [String: URLSuggestion] = [:]
        for (label, url) in remoteURLs {
            let ep = Endpoint.parse(url)
            guard ep.scheme == "https", let base = ep.httpsBase, cert.covers(ep.host) else { continue }
            found[base, default: URLSuggestion(url: base, remotes: [])].remotes.append(label)
        }
        if !found.isEmpty {
            return found.values.sorted { ($0.remotes.count, $1.url) > ($1.remotes.count, $0.url) }
        }
        return cert.names.map { URLSuggestion(url: Endpoint.normaliseServerURL("https://\($0)/"), remotes: []) }
    }

    /// The trusted (url, certPath) git would apply to `url`, if any.
    public static func server(for url: String, in servers: [(String, String)]) -> (String, String)? {
        let ep = Endpoint.parse(url)
        var best: (String, String)?
        for (srvURL, path) in servers {
            let s = Endpoint.parse(srvURL)
            guard s.scheme == ep.scheme, s.host.lowercased() == ep.host.lowercased(),
                  s.effectivePort == ep.effectivePort,
                  (ep.path.isEmpty ? "/" : ep.path).hasPrefix(s.path.isEmpty ? "/" : s.path) else { continue }
            if best == nil || srvURL.count > best!.0.count { best = (srvURL, path) }
        }
        return best
    }

    public static func fileName(for url: String) -> String {
        let ep = Endpoint.parse(Endpoint.normaliseServerURL(url))
        let host = String(ep.host.map { $0.isLetter || $0.isNumber || "._-".contains($0) ? $0 : "_" })
        return "\(host.isEmpty ? "server" : host)-\(ep.effectivePort ?? 443).pem"
    }
}
