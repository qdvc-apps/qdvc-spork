import Foundation

/// A git failure, with the stderr text and a human hint when we recognise it.
public struct GitError: Error, LocalizedError, Sendable {
    public let args: [String]
    public let code: Int32
    public let stderr: String

    public init(args: [String], code: Int32, stderr: String) {
        self.args = args
        self.code = code
        self.stderr = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public var errorDescription: String? {
        stderr.isEmpty ? "git \(args.first ?? "") exited with status \(code)" : stderr
    }

    public var hint: String? { GitError.hint(for: stderr) }

    public static func hint(for stderr: String) -> String? {
        let s = stderr.lowercased()
        func has(_ needles: String...) -> Bool { needles.contains { s.contains($0) } }
        if has("ssl certificate problem", "self-signed certificate", "self signed certificate",
               "unable to get local issuer") {
            return "The server's HTTPS certificate isn't trusted. Add the server's cert.pem in Settings \u{203A} Servers."
        }
        if has("certificate subject name", "no alternative certificate subject name") {
            return "The certificate doesn't cover this host name. Use the exact name it was issued for."
        }
        if has("authentication failed", "could not read username", "could not read password",
               "terminal prompts disabled") {
            return "Authentication failed. Check your user name, password or access token."
        }
        if has("permission denied (publickey") {
            return "The SSH server rejected your key. Is it loaded in ssh-agent (ssh-add)?"
        }
        if has("host key verification failed") {
            return "SSH couldn't verify the server's host key. Check ~/.ssh/known_hosts."
        }
        if has("could not resolve host", "nodename nor servname", "name or service not known") {
            return "The server's name couldn't be resolved. Are you on the right network?"
        }
        if has("connection refused", "timed out", "no route to host", "network is unreachable") {
            return "The server didn't answer. It may be offline."
        }
        if has("non-fast-forward", "fetch first", "[rejected]") {
            return "The remote has commits you don't have. Pull first, then push again."
        }
        if has("not possible to fast-forward", "diverging branches") {
            return "Your branch and the remote have diverged. Choose Merge or Rebase."
        }
        if has("please tell me who you are") {
            return "Git doesn't know your name and email. Set user.name and user.email."
        }
        if has("gpg failed to sign", "cannot run gpg") {
            return "Commit signing failed. Check that gpg works outside Spork, or turn off commit.gpgsign."
        }
        return nil
    }
}

public struct GitResult: Sendable {
    public let code: Int32
    public let stdout: Data
    public let stderr: Data
    public var text: String { String(decoding: stdout, as: UTF8.self) }
    public var err: String { String(decoding: stderr, as: UTF8.self) }
}

public struct TrustedServer: Hashable, Sendable {
    public let url: String        // normalised, e.g. "https://myserver.local:3000/"
    public let certPath: String
    public init(url: String, certPath: String) {
        self.url = url
        self.certPath = certPath
    }
}

/// Runs git with the environment Spork needs. Blocking; call off the main thread.
public final class GitRunner: @unchecked Sendable {
    public var gitPath: String
    public var askpass: String?
    public var servers: [TrustedServer] = []
    public var defaultTimeout: TimeInterval = 60
    public var networkTimeout: TimeInterval = 180

    public init(gitPath: String? = nil, askpass: String? = nil) {
        self.gitPath = gitPath ?? GitRunner.findGit()
        self.askpass = askpass
    }

    /// GUI apps get a minimal PATH, so look in the usual places ourselves.
    public static func findGit() -> String {
        let candidates = ["/opt/homebrew/bin/git", "/usr/local/bin/git", "/usr/bin/git"]
        for c in candidates where FileManager.default.isExecutableFile(atPath: c) { return c }
        return "/usr/bin/git"
    }

    public func environment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let extraPath = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
        var path = (env["PATH"] ?? "").split(separator: ":").map(String.init)
        for p in extraPath where !path.contains(p) { path.append(p) }
        env["PATH"] = path.joined(separator: ":")
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_PAGER"] = "cat"
        env["PAGER"] = "cat"
        env["GIT_EDITOR"] = "true"
        env["GIT_MERGE_AUTOEDIT"] = "no"
        env["GIT_OPTIONAL_LOCKS"] = "0"
        env["LC_ALL"] = "en_US.UTF-8"
        if let a = askpass {
            env["GIT_ASKPASS"] = a
            env["SSH_ASKPASS"] = a
            env["SSH_ASKPASS_REQUIRE"] = "force"   // OpenSSH 8.4+: use askpass even without DISPLAY
        }
        return env
    }

    public func configFlags() -> [String] {
        var f = ["-c", "core.quotepath=false", "-c", "color.ui=false", "-c", "core.pager=cat",
                 "-c", "log.showSignature=false", "-c", "diff.noprefix=false",
                 "-c", "diff.mnemonicPrefix=false"]
        for s in servers {
            f += ["-c", "http.\(s.url).sslCAInfo=\(s.certPath)"]
        }
        return f
    }

    @discardableResult
    public func run(_ args: [String], cwd: String? = nil, input: Data? = nil, check: Bool = true,
                    network: Bool = false, timeout: TimeInterval? = nil,
                    okCodes: Set<Int32> = [0]) throws -> GitResult {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: gitPath)
        process.arguments = configFlags() + args
        if let cwd { process.currentDirectoryURL = URL(fileURLWithPath: cwd) }
        process.environment = environment()
        let outPipe = Pipe()
        let errPipe = Pipe()
        process.standardOutput = outPipe
        process.standardError = errPipe
        let inPipe = input != nil ? Pipe() : nil
        if let inPipe { process.standardInput = inPipe } else {
            process.standardInput = FileHandle.nullDevice
        }
        do {
            try process.run()
        } catch {
            throw GitError(args: args, code: -1, stderr: "Couldn't run git at \(gitPath): \(error.localizedDescription)")
        }

        final class Box: @unchecked Sendable { var data = Data() }
        let out = Box()
        let err = Box()
        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            out.data = outPipe.fileHandleForReading.readDataToEndOfFile()
            group.leave()
        }
        group.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            err.data = errPipe.fileHandleForReading.readDataToEndOfFile()
            group.leave()
        }
        if let inPipe, let input {
            DispatchQueue.global(qos: .userInitiated).async {
                inPipe.fileHandleForWriting.write(input)
                try? inPipe.fileHandleForWriting.close()
            }
        }
        let limit = timeout ?? (network ? networkTimeout : defaultTimeout)
        if group.wait(timeout: .now() + limit) == .timedOut {
            process.terminate()
            _ = group.wait(timeout: .now() + 5)
            throw GitError(args: args, code: -1, stderr: "git \(args.first ?? "") timed out after \(Int(limit)) s")
        }
        process.waitUntilExit()
        let result = GitResult(code: process.terminationStatus, stdout: out.data, stderr: err.data)
        if check && !okCodes.contains(result.code) {
            throw GitError(args: args, code: result.code, stderr: result.err)
        }
        return result
    }

    public func version() -> String? {
        (try? run(["--version"]))?.text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
