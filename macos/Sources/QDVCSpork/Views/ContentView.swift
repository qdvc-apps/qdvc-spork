import AppKit
import SporkCore
import SwiftUI

/// Sidebar of bookmarks | dashboard or repository. Built on the structure that
/// avoided the sidebar/window-button glitch in sibling apps: a NavigationSplitView
/// with no column-visibility binding and right-hand toolbar items only.
struct ContentView: View {
    @Bindable var model: AppModel

    var body: some View {
        NavigationSplitView {
            SidebarView(model: model)
                .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 340)
        } detail: {
            detail
        }
        .overlay(alignment: .bottom) {
            if let t = model.toast { ToastView(text: t) }
        }
        .animation(.easeOut(duration: 0.2), value: model.toast)
        .alert(model.alert?.title ?? "", isPresented: Binding(
            get: { model.alert != nil }, set: { if !$0 { model.alert = nil } }),
               presenting: model.alert) { a in
            if a.actions.isEmpty {
                Button("OK", role: .cancel) {}
            } else {
                ForEach(Array(a.actions.enumerated()), id: \.offset) { _, action in
                    Button(action.0, role: isDestructive(action.0) ? ButtonRole.destructive : nil) { action.1() }
                }
                Button("Cancel", role: .cancel) {}
            }
        } message: { a in
            Text(a.detail.isEmpty ? a.message : "\(a.message)\n\n\(a.detail.prefix(800))")
        }
        .frame(minWidth: 960, minHeight: 560)
    }

    func isDestructive(_ title: String) -> Bool {
        title.hasPrefix("Discard") || title.hasPrefix("Delete") || title == "Remove"
    }

    @ViewBuilder var detail: some View {
        if let sel = model.selection, sel != dashboardID,
           model.settings.bookmarks.contains(where: { $0.path == sel }) {
            RepoView(app: model, repo: model.repoModel(for: sel))
                .id(sel)
        } else {
            DashboardView(model: model)
        }
    }
}

struct SidebarView: View {
    @Bindable var model: AppModel

    var body: some View {
        List(selection: Binding(guarded: { model.selection }, set: { model.selection = $0 })) {
            Label("Dashboard", systemImage: "square.grid.2x2")
                .tag(dashboardID)
            Section("Repositories") {
                ForEach(model.settings.bookmarks) { b in
                    SidebarRow(bookmark: b, summary: model.summaries[b.path])
                        .tag(b.path)
                        .contextMenu { menu(for: b) }
                }
            }
        }
        .listStyle(.sidebar)
        .overlay {
            if model.settings.bookmarks.isEmpty {
                VStack(spacing: 10) {
                    Text("No Repositories").font(.headline)
                    Text("Add one, drop a folder here, or run spork . in Terminal.")
                        .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    Button("Add Repository…") { chooseRepository(model) }
                }
                .padding()
            }
        }
        .safeAreaInset(edge: .bottom) {
            HStack {
                Button { chooseRepository(model) } label: { Image(systemName: "plus") }
                    .buttonStyle(.borderless)
                    .help("Add Repository (⌘O)")
                Spacer()
            }
            .padding(8)
        }
        .dropDestination(for: URL.self) { urls, _ in
            for u in urls { model.open(path: u.path) }
            return !urls.isEmpty
        }
    }

    @ViewBuilder func menu(for b: Bookmark) -> some View {
        Button("Fetch") { model.repoModel(for: b.path).fetch(nil) }
        Button("Show in Finder") { Workspace.reveal(b.path) }
        Button("Open in Terminal") { Workspace.openTerminal(b.path, app: model.settings.terminal) }
        Button("Copy Path") { Workspace.copy(b.path) }
        Divider()
        Button("Move Up") { model.moveBookmark(b.path, by: -1) }
        Button("Move Down") { model.moveBookmark(b.path, by: 1) }
        Divider()
        Button("Remove from Spork…") {
            model.alert = AppAlert(title: "Remove \(b.displayName) from Spork?",
                                   message: "Only the bookmark is removed. The folder stays on disk.",
                                   actions: [("Remove", { model.removeBookmark(b.path) })])
        }
    }
}

struct SidebarRow: View {
    let bookmark: Bookmark
    let summary: RepoSummary?

    var body: some View {
        HStack(spacing: 8) {
            StatusDot(level: summary?.level)
            VStack(alignment: .leading, spacing: 1) {
                Text(bookmark.displayName).lineLimit(1)
                if let sub = subtitle {
                    Text(sub).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
        }
        .badge(badge)
        .help(tooltip)
    }

    var subtitle: String? {
        guard let s = summary else { return nil }
        if s.level == .missing { return "Not found" }
        if let b = s.branch { return b + (s.operation.map { " · \($0)" } ?? "") }
        return s.detached ? "Detached HEAD" : nil
    }

    var badge: Text? {
        guard let s = summary else { return nil }
        if s.level == .missing { return Text("!") }
        var parts: [String] = []
        if s.totalAhead > 0 { parts.append("↑\(s.totalAhead)") }
        if s.totalBehind > 0 { parts.append("↓\(s.totalBehind)") }
        if s.dirtyCount > 0 { parts.append("●\(s.dirtyCount)") }
        if s.level == .unknown && parts.isEmpty { parts.append("?") }
        return parts.isEmpty ? nil : Text(parts.joined(separator: " "))
    }

    var tooltip: String {
        guard let s = summary else { return bookmark.path }
        let detail = s.reasons.isEmpty ? (s.level == .unknown ? "Some remotes couldn't be checked" : "Clean and in sync")
            : s.reasons.joined(separator: "\n")
        return bookmark.path + "\n" + detail
    }
}

@MainActor
func chooseRepository(_ model: AppModel) {
    let panel = NSOpenPanel()
    panel.canChooseDirectories = true
    panel.canChooseFiles = false
    panel.allowsMultipleSelection = true
    panel.prompt = "Add"
    panel.message = "Choose one or more Git repositories"
    if panel.runModal() == .OK {
        for url in panel.urls { model.open(path: url.path) }
    }
}
