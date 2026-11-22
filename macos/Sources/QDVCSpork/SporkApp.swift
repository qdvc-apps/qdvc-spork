import AppKit
import SporkCore
import SwiftUI

@main
struct SporkApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    private let model = AppModel.shared

    var body: some Scene {
        // A single `Window`, not a WindowGroup: WindowGroup windows always add a
        // "+" button to the tab bar and get restored as duplicates (Cursed Knowledge).
        Window("QDVC Spork", id: "main") {
            ContentView(model: model)
        }
        .defaultSize(width: 1280, height: 800)
        .commands { SporkCommands(model: model) }

        Settings {
            SettingsView(model: model)
        }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // `swift run QDVCSpork /path/to/repo` or `open -a ... --args /path`
        let args = CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-") }
        for a in args where FileManager.default.fileExists(atPath: a) {
            MainActor.assumeIsolated { AppModel.shared.open(path: a) }
        }
    }

    /// `spork .` runs `open -b org.qdvc.spork.mac <folder>`, which lands here,
    /// as do folders dropped on the Dock icon.
    func application(_ application: NSApplication, open urls: [URL]) {
        MainActor.assumeIsolated {
            for url in urls where url.isFileURL { AppModel.shared.open(path: url.path) }
        }
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

/// Menu commands. Marked @MainActor and handed the model explicitly: Commands
/// structs aren't views, and environment lookups there aren't reliable
/// (Cursed Knowledge).
@MainActor
struct SporkCommands: Commands {
    @Bindable var model: AppModel

    var current: RepoModel? {
        guard let sel = model.selection, sel != dashboardID,
              model.settings.bookmarks.contains(where: { $0.path == sel }) else { return nil }
        return model.repoModel(for: sel)
    }

    var body: some Commands {
        CommandGroup(replacing: .newItem) {
            Button("Add Repository…") { chooseRepository(model) }
                .keyboardShortcut("o")
        }
        CommandGroup(after: .appSettings) {
            Button("Install Command Line Tool…") {
                do {
                    let url = try Workspace.installCLI()
                    model.show("Installed \(url.path)")
                } catch {
                    model.alert = AppAlert(title: "Couldn't Install the Command", error: error)
                }
            }
        }
        CommandGroup(after: .sidebar) {
            Button("Dashboard") { model.selection = dashboardID }
                .keyboardShortcut("0")
            ForEach(Array(model.settings.bookmarks.prefix(9).enumerated()), id: \.offset) { i, b in
                Button(b.displayName) { model.selection = b.path }
                    .keyboardShortcut(KeyEquivalent(Character(String(i + 1))))
            }
            Divider()
            Button("Commit") { current?.tab = .commit; current?.loadTab() }
                .keyboardShortcut("1", modifiers: [.command, .option])
                .disabled(current == nil)
            Button("Changes") { current?.tab = .changes; current?.loadTab() }
                .keyboardShortcut("2", modifiers: [.command, .option])
                .disabled(current == nil)
            Button("Browse") { current?.tab = .browse; current?.loadTab() }
                .keyboardShortcut("3", modifiers: [.command, .option])
                .disabled(current == nil)
            Divider()
        }
        CommandMenu("Repository") {
            Button("Fetch All Remotes") { current?.fetch(nil) }
                .keyboardShortcut("t", modifiers: [.command, .shift])
                .disabled(current == nil)
            Button("Pull") { current?.pull(nil) }
                .keyboardShortcut("p", modifiers: [.command, .shift])
                .disabled(current?.branch == nil)
            Button("Push") { if let r = current?.defaultRemote { current?.push([r]) } }
                .keyboardShortcut("p")
                .disabled(current?.branch == nil)
            Button("Push to All Remotes") { if let c = current { c.push(c.remotes.map(\.name)) } }
                .keyboardShortcut("p", modifiers: [.command, .option])
                .disabled((current?.remotes.count ?? 0) < 2)
            Divider()
            Button("New Branch…") { NotificationCenter.default.post(name: .sporkNewBranch, object: nil) }
                .keyboardShortcut("n", modifiers: [.command, .shift])
                .disabled(current == nil)
            Button("Refresh") { current?.refresh(full: true) }
                .keyboardShortcut("r")
                .disabled(current == nil)
            Divider()
            Button("Show in Finder") { if let p = current?.path { Workspace.reveal(p) } }
                .keyboardShortcut("r", modifiers: [.command, .shift])
                .disabled(current == nil)
            Button("Open in Terminal") { if let p = current?.path { Workspace.openTerminal(p, app: model.settings.terminal) } }
                .keyboardShortcut("t", modifiers: [.command, .option, .shift])
                .disabled(current == nil)
            Divider()
            Button("Fetch All Repositories") { model.fetchEverything() }
                .keyboardShortcut("t", modifiers: [.command, .option])
                .disabled(model.fetching)
        }
    }
}
