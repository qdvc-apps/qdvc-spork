import Foundation
import XCTest
@testable import SporkCore

final class ParserTests: XCTestCase {
    func testStatusV2() {
        let data = [
            "# branch.oid 1234567890abcdef1234567890abcdef12345678",
            "# branch.head main",
            "# branch.upstream origin/main",
            "# branch.ab +2 -1",
            "1 M. N... 100644 100644 100644 aaa bbb staged.txt",
            "1 .M N... 100644 100644 100644 aaa bbb dir/with space.txt",
            "2 R. N... 100644 100644 100644 aaa bbb R100 new.txt", "old.txt",
            "u UU N... 100644 100644 100644 100644 a b c conflict.txt",
            "? untracked.txt", "",
        ].joined(separator: "\0")
        let st = Parsers.parseStatusV2(data)
        XCTAssertEqual(st.branch.head, "main")
        XCTAssertEqual(st.branch.ahead, 2)
        XCTAssertEqual(st.branch.behind, 1)
        XCTAssertEqual(st.staged.map(\.path), ["staged.txt", "new.txt"])
        XCTAssertEqual(st.unstaged.map(\.path), ["dir/with space.txt", "conflict.txt", "untracked.txt"])
        XCTAssertEqual(st.entries[2].oldPath, "old.txt")
    }

    func testNameStatusAndNumstat() {
        let changes = Parsers.parseNameStatus("M\0a.txt\0R087\0old.c\0new.c\0D\0gone\0")
        XCTAssertEqual(changes.map(\.path), ["a.txt", "new.c", "gone"])
        XCTAssertEqual(changes[1].oldPath, "old.c")
        let stats = Parsers.parseNumstat("3\t1\ta.txt\0" + "5\t2\t\0old.c\0new.c\0" + "-\t-\tgone\0")
        XCTAssertEqual(stats["a.txt"]?.0, 3)
        XCTAssertEqual(stats["new.c"]?.1, 2)
        XCTAssertNil(stats["gone"]?.0)
    }

    func testDiffWithCRLFAndNoNewline() {
        let text = [
            "diff --git a/x.txt b/x.txt", "index 1..2 100644", "--- a/x.txt", "+++ b/x.txt",
            "@@ -1,2 +1,2 @@", " keep\r", "-old", "\\ No newline at end of file", "+new",
            "\\ No newline at end of file", "",
        ].joined(separator: "\n")
        let files = Parsers.parseDiff(text)
        XCTAssertEqual(files.count, 1)
        let kinds = files[0].hunks[0].lines.map { String($0.kind) }
        XCTAssertEqual(kinds, [" ", "-", "\\", "+", "\\"])
        XCTAssertEqual(files[0].hunks[0].lines[0].newNo, 1)
        XCTAssertEqual(files[0].newPath, "x.txt")
    }

    func testRefs() {
        let labels = Parsers.parseRefs(["HEAD -> refs/heads/main", "tag: refs/tags/v1",
                                        "refs/remotes/origin/main", "refs/remotes/origin/HEAD"])
        XCTAssertEqual(labels.map(\.name), ["main", "v1", "origin/main"])
        XCTAssertEqual(labels.map(\.kind), [.head, .tag, .remote])
    }

    func testUnquote() {
        XCTAssertEqual(Parsers.unquoteC(#"caf\303\251 \"x\""#), "café \"x\"")
    }
}

final class GraphTests: XCTestCase {
    func c(_ sha: String, _ parents: String...) -> Commit {
        Commit(sha: sha, parents: parents, authorName: "a", authorEmail: "a", authorTime: 0,
               committerName: "a", committerEmail: "a", committerTime: 0, refs: [], subject: sha)
    }

    func testMergeLayoutMatchesGTKEdition() {
        let rows = GraphBuilder().add([c("m", "b", "f"), c("f", "a"), c("b", "a"), c("a")])
        XCTAssertEqual(rows.map(\.node), [0, 1, 0, 0])
        XCTAssertTrue(rows[2].bottom.contains { $0.from == 1 && $0.to == 0 })
        XCTAssertEqual(rows.map(\.width).max(), 2)
    }
}

final class EndpointTests: XCTestCase {
    func testParse() {
        let e = Endpoint.parse("https://myserver.local:3000/blair/x.git")
        XCTAssertEqual(e.httpsBase, "https://myserver.local:3000/")
        let s = Endpoint.parse("git@github.com:qdvc-apps/x.git")
        XCTAssertEqual(s.scheme, "ssh")
        XCTAssertEqual(s.host, "github.com")
        XCTAssertEqual(s.user, "git")
        XCTAssertEqual(Endpoint.parse("ssh://git@myserver.local:222/x.git").serverKey, "myserver.local:222")
        XCTAssertTrue(Endpoint.parse("/srv/git/x.git").isLocal)
        XCTAssertEqual(Endpoint.normaliseServerURL("MyServer.local:3000"), "https://myserver.local:3000/")
    }
}

final class CertificateTests: XCTestCase {
    // openssl req -x509 -newkey ec ... -subj /CN=myserver.local
    //   -addext subjectAltName=DNS:myserver.local,DNS:myserver,IP:192.168.1.20
    static let pem = """
    -----BEGIN CERTIFICATE-----
    MIIBsDCCAVWgAwIBAgIUS7RKKOhyT+84ceDkBWJ83o3m4EQwCgYIKoZIzj0EAwIw
    GTEXMBUGA1UEAwwObXlzZXJ2ZXIubG9jYWwwHhcNMjYxMTIyMTAxMzE5WhcNMzEx
    MTIxMTAxMzE5WjAZMRcwFQYDVQQDDA5teXNlcnZlci5sb2NhbDBZMBMGByqGSM49
    AgEGCCqGSM49AwEHA0IABIOetR1BVDqCpC0OR0jV+6rL5mFp/oCpObzZfONVaoGK
    bI8dAdWuGER8oU74SPqne2tCeIGe+B7VqmGMr/0lrf+jezB5MB0GA1UdDgQWBBS7
    DM8QWBnIrpROZOmwEj+bEQoO6jAfBgNVHSMEGDAWgBS7DM8QWBnIrpROZOmwEj+b
    EQoO6jApBgNVHREEIjAggg5teXNlcnZlci5sb2NhbIIIbXlzZXJ2ZXKHBMCoARQw
    DAYDVR0TAQH/BAIwADAKBggqhkjOPQQDAgNJADBGAiEAw7SBwy/0lg0cpS+5T9Cr
    nNeNfLHrnxcUysse8GqVSvYCIQDXap4Fc9f8f4CnR122UoEUqGmzg1wwhE5q0i8v
    fTNU3g==
    -----END CERTIFICATE-----
    """

    func testParse() throws {
        let info = try XCTUnwrap(Certificates.load(Data(Self.pem.utf8)).first)
        XCTAssertEqual(info.subjectCN, "myserver.local")
        XCTAssertEqual(info.names, ["myserver.local", "myserver", "192.168.1.20"])
        XCTAssertFalse(info.isCA)
        XCTAssertEqual(info.notAfter.timeIntervalSince1970, 1953022399)
        #if canImport(CryptoKit)
        XCTAssertEqual(info.sha256, "CE:92:68:DE:C5:58:09:E3:30:D9:F0:63:F6:00:C5:80:87:41:6F:D1:2D:01:61:76:E1:72:78:A9:87:78:29:0A")
        #endif
        XCTAssertTrue(info.covers("MyServer.local"))
        let sugg = Certificates.suggestServerURLs(info, remoteURLs: [
            "dotfiles: nas": "https://myserver.local:3000/blair/dotfiles.git",
            "notes: github": "https://github.com/blair/notes.git",
        ])
        XCTAssertEqual(sugg.map(\.url), ["https://myserver.local:3000/"])
    }

    func testPrivateKeyRejected() {
        let key = "-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n"
        XCTAssertThrowsError(try Certificates.load(Data(key.utf8)))
    }
}

/// Integration tests against the real git command.
final class RepositoryTests: XCTestCase {
    var root: URL!
    var runner: GitRunner!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("spork-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        setenv("GIT_CONFIG_GLOBAL", "/dev/null", 1)
        setenv("GIT_CONFIG_NOSYSTEM", "1", 1)
        setenv("GIT_AUTHOR_NAME", "Ada Tester", 1)
        setenv("GIT_AUTHOR_EMAIL", "ada@example.org", 1)
        setenv("GIT_COMMITTER_NAME", "Ada Tester", 1)
        setenv("GIT_COMMITTER_EMAIL", "ada@example.org", 1)
        runner = GitRunner()
    }

    func sh(_ dir: URL, _ args: String...) throws {
        try runner.run(args, cwd: dir.path)
    }

    func testCommitStageHunkAndRemotes() throws {
        let work = root.appendingPathComponent("work")
        let bare = root.appendingPathComponent("alpha.git")
        try runner.run(["init", "-q", "-b", "main", work.path])
        try runner.run(["init", "-q", "--bare", "-b", "main", bare.path])
        let lines = (1...40).map { "line \($0)" }
        try (lines.joined(separator: "\n") + "\n").write(to: work.appendingPathComponent("f.txt"), atomically: true, encoding: .utf8)
        let repo = Repository(runner: runner, path: work.path)
        try repo.stage(["f.txt"])
        try repo.commit(message: "base\n")
        var changed = lines
        changed[1] = "CHANGED 2"
        changed[35] = "CHANGED 36"
        try (changed.joined(separator: "\n") + "\n").write(to: work.appendingPathComponent("f.txt"), atomically: true, encoding: .utf8)
        let fd = try XCTUnwrap(repo.unstagedDiff("f.txt").first)
        XCTAssertEqual(fd.hunks.count, 2)
        try repo.stageHunk(fd, 1)
        XCTAssertEqual(try repo.stagedDiff("f.txt").first?.hunks.count, 1)
        try repo.commit(message: "second\n")
        XCTAssertEqual(try repo.log(LogQuery()).map(\.subject), ["second", "base"])

        try sh(work, "remote", "add", "alpha", bare.path)
        try repo.push("alpha", branch: "main", setUpstream: true)
        try repo.stage(["f.txt"])
        try repo.commit(message: "third\n")
        let states = try repo.remoteStates(branch: "main")
        XCTAssertEqual(states.first?.ahead, 1)
        let s = Census.summarize(path: work.path, runner: runner)
        XCTAssertEqual(s.level, .attention)
        XCTAssertTrue(s.reasons.contains("1 to push to alpha"))
    }

    func testSslCAInfoMatchesByURL() throws {
        let r = GitRunner()
        r.servers = [TrustedServer(url: "https://myserver.local:3000/", certPath: "/tmp/my.pem")]
        let out = try r.run(["config", "--get-urlmatch", "http.sslCAInfo",
                             "https://myserver.local:3000/blair/dotfiles.git"])
        XCTAssertEqual(out.text.trimmingCharacters(in: .whitespacesAndNewlines), "/tmp/my.pem")
    }
}
