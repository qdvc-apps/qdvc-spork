import AppKit
import SporkCore

/// Opening things in other apps, the clipboard, and the `spork` command.
enum Workspace {
    static let bundleID = "org.qdvc.spork.mac"

    static func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    static func reveal(_ path: String) {
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
    }

    static func open(_ path: String, withApp app: String = "") {
        let url = URL(fileURLWithPath: path)
        if let appURL = applicationURL(app) {
            NSWorkspace.shared.open([url], withApplicationAt: appURL,
                                    configuration: NSWorkspace.OpenConfiguration())
        } else {
            NSWorkspace.shared.open(url)
        }
    }

    /// Opens a folder in Terminal (or the terminal app chosen in Settings).
    static func openTerminal(_ path: String, app: String = "") {
        let fallback = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
        let appURL = applicationURL(app) ?? fallback
        NSWorkspace.shared.open([URL(fileURLWithPath: path, isDirectory: true)], withApplicationAt: appURL,
                                configuration: NSWorkspace.OpenConfiguration())
    }

    /// `app` may be a bundle identifier (com.googlecode.iterm2) or a path to an .app.
    static func applicationURL(_ app: String) -> URL? {
        let a = app.trimmingCharacters(in: .whitespaces)
        if a.isEmpty { return nil }
        if a.hasSuffix(".app") || a.hasPrefix("/") {
            return FileManager.default.fileExists(atPath: a) ? URL(fileURLWithPath: a) : nil
        }
        return NSWorkspace.shared.urlForApplication(withBundleIdentifier: a)
    }

    static func relative(_ epoch: Int?) -> String {
        guard let epoch, epoch > 0 else { return "" }
        let date = Date(timeIntervalSince1970: TimeInterval(epoch))
        if Date().timeIntervalSince(date) < 60 { return "just now" }
        return relativeFormatter.localizedString(for: date, relativeTo: Date())
    }

    static let relativeFormatter: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .full
        return f
    }()

    static func fullDate(_ epoch: Int) -> String {
        let f = DateFormatter()
        f.dateStyle = .full
        f.timeStyle = .short
        return f.string(from: Date(timeIntervalSince1970: TimeInterval(epoch)))
    }

    static func plural(_ n: Int, _ word: String, _ pluralWord: String? = nil) -> String {
        "\(n) \(n == 1 ? word : (pluralWord ?? word + "s"))"
    }

    // MARK: the spork command

    static var cliScript: String {
        """
        #!/bin/sh
        # spork — open a Git repository in QDVC Spork.  Usage: spork [path]
        # Installed by QDVC Spork.
        target="${1:-.}"
        if [ ! -e "$target" ]; then
            echo "spork: $target: no such file or directory" >&2
            exit 2
        fi
        dir=$(cd "$target" 2>/dev/null && pwd -P) || dir=$(cd "$(dirname "$target")" && pwd -P)
        top=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || {
            echo "spork: $dir is not inside a Git repository" >&2
            exit 1
        }
        open -b \(bundleID) "$top" 2>/dev/null || {
            echo "spork: QDVC Spork isn't installed (run macos/scripts/build-app.sh --install)" >&2
            exit 1
        }

        """
    }

    /// /usr/local/bin when it's writable, otherwise ~/.local/bin.
    static var cliDestination: URL {
        let usrLocal = URL(fileURLWithPath: "/usr/local/bin")
        if FileManager.default.isWritableFile(atPath: usrLocal.path) {
            return usrLocal.appendingPathComponent("spork")
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".local/bin/spork")
    }

    static func installCLI() throws -> URL {
        let dest = cliDestination
        try FileManager.default.createDirectory(at: dest.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try cliScript.write(to: dest, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: dest.path)
        return dest
    }
}
