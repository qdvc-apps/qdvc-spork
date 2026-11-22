import AppKit
import Combine
import SporkCore
import SwiftUI

/// Commit list | detail tabs, plus the repository's toolbar.
struct RepoView: View {
    @Bindable var app: AppModel
    @Bindable var repo: RepoModel
    @AppStorage("commitListWidth") private var listWidth = 400.0
    @State private var newBranchName = ""
    @State private var showNewBranch = false

    var body: some View {
        ResizableHSplit(leadingWidth: $listWidth, minLeading: 280, minTrailing: 420) {
            CommitListView(repo: repo)
        } trailing: {
            DetailView(repo: repo)
        }
        .navigationTitle(repo.name)
        .navigationSubtitle(subtitle)
        .searchable(text: $repo.searchText, placement: .toolbar, prompt: "Search Commits")
        .searchScopes($repo.searchField) {
            ForEach(SearchField.allCases) { f in Text(f.title).tag(f) }
        }
        .onChange(of: "\(repo.searchText)\u{0}\(repo.searchField.rawValue)") { _, _ in
            // Debounce typing, then reload the list.
            searchTask?.cancel()
            searchTask = Task {
                try? await Task.sleep(nanoseconds: 300_000_000)
                if !Task.isCancelled { repo.reloadLog() }
            }
        }
        .toolbar { toolbar }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            repo.refresh()
        }
        .alert("New Branch", isPresented: $showNewBranch) {
            TextField("feature/my-change", text: $newBranchName)
            Button("Create") {
                let name = newBranchName.trimmingCharacters(in: .whitespaces)
                if !name.isEmpty {
                    let start = repo.selection == workingCopyID ? nil : repo.selection
                    repo.createBranch(name, at: start)
                }
                newBranchName = ""
            }
            Button("Cancel", role: .cancel) { newBranchName = "" }
        } message: {
            Text(repo.selection == workingCopyID || repo.selection == nil
                 ? "The new branch starts at the current commit."
                 : "The new branch starts at the selected commit.")
        }
        .onReceive(NotificationCenter.default.publisher(for: .sporkNewBranch)) { _ in showNewBranch = true }
    }

    @State private var searchTask: Task<Void, Never>?

    var subtitle: String {
        var parts: [String] = []
        if let st = repo.status {
            if let b = st.branch.head { parts.append(b) }
            else if let oid = st.branch.oid { parts.append("Detached at \(oid.prefix(8))") }
        }
        if let op = repo.operation { parts.append("\(op.capitalized) in progress") }
        return parts.joined(separator: " · ")
    }

    @ToolbarContentBuilder var toolbar: some ToolbarContent {
        ToolbarItem(placement: .primaryAction) {
            Menu {
                Section("Branches") {
                    ForEach(repo.branches.prefix(40)) { b in
                        Button {
                            repo.switchBranch(b.name)
                        } label: {
                            if b.isHead { Label(b.name, systemImage: "checkmark") } else { Text(b.name) }
                        }
                    }
                }
                Divider()
                Button("New Branch…") { showNewBranch = true }
                let others = repo.branches.filter { !$0.isHead }
                if !others.isEmpty {
                    Menu("Delete Branch") {
                        ForEach(others.prefix(40)) { b in
                            Button(b.name) {
                                app.alert = AppAlert(title: "Delete Branch \(b.name)?",
                                                     message: "The branch label is removed. Commits on other branches stay.",
                                                     actions: [("Delete", { repo.deleteBranch(b.name) })])
                            }
                        }
                    }
                }
            } label: {
                Label(repo.branch ?? "Branch", systemImage: "arrow.triangle.branch")
                    .labelStyle(.titleAndIcon)
            }
            .help("Switch Branch")
        }
        ToolbarItemGroup(placement: .primaryAction) {
            if repo.busy > 0 {
                ProgressView().controlSize(.small)
            }
            Menu {
                ForEach(repo.remotes) { r in Button("Fetch from \(r.name)") { repo.fetch(r.name) } }
            } label: {
                Label("Fetch", systemImage: "arrow.triangle.2.circlepath")
            } primaryAction: {
                repo.fetch(nil)
            }
            .help("Fetch All Remotes (⇧⌘T)")
            .disabled(repo.remotes.isEmpty)
            Menu {
                ForEach(repo.remotes) { r in Button("Pull from \(r.name)") { repo.pull(r.name) } }
            } label: {
                Label("Pull", systemImage: "arrow.down")
            } primaryAction: {
                repo.pull(nil)
            }
            .help("Pull (⇧⌘P)")
            .disabled(repo.remotes.isEmpty || repo.branch == nil)
            Menu {
                ForEach(repo.remotes) { r in Button("Push to \(r.name)") { repo.push([r.name]) } }
                if repo.remotes.count > 1 {
                    Divider()
                    Button("Push to All Remotes") { repo.push(repo.remotes.map(\.name)) }
                }
            } label: {
                Label("Push", systemImage: "arrow.up")
            } primaryAction: {
                if let r = repo.defaultRemote { repo.push([r]) }
            }
            .help("Push (⌘P)")
            .disabled(repo.remotes.isEmpty || repo.branch == nil)
            Menu {
                Toggle("Show All Branches", isOn: Binding(
                    guarded: { app.settings.showAllBranches },
                    set: { app.settings.showAllBranches = $0; app.save(); repo.reloadLog() }))
                Button("Refresh") { repo.refresh(full: true) }
                Divider()
                Button("Show in Finder") { Workspace.reveal(repo.path) }
                Button("Open in Terminal") { Workspace.openTerminal(repo.path, app: app.settings.terminal) }
                Button("Copy Path") { Workspace.copy(repo.path) }
                if !repo.remotes.isEmpty {
                    Menu("Copy Remote URL") {
                        ForEach(repo.remotes) { r in Button(r.name) { Workspace.copy(r.fetchURL) } }
                    }
                }
            } label: {
                Label("More", systemImage: "ellipsis.circle")
            }
            .help("Repository")
        }
    }
}

extension Notification.Name {
    static let sporkNewBranch = Notification.Name("SporkNewBranch")
}

// MARK: - commit list

struct CommitListView: View {
    @Bindable var repo: RepoModel
    static let rowHeight: CGFloat = 40

    var body: some View {
        VStack(spacing: 0) {
            if !repo.remoteStates.isEmpty {
                RemoteStrip(repo: repo)
                Divider()
            }
            if repo.commits.isEmpty && !repo.showsWorkingRow {
                emptyState(repo.searchText.isEmpty ? "No Commits" : "No Matches",
                           repo.searchText.isEmpty ? "clock" : "magnifyingglass",
                           repo.searchText.isEmpty ? "This branch has no commits yet." : "Nothing matches your search.")
            } else {
                list
            }
        }
    }

    var list: some View {
        List(selection: Binding(guarded: { repo.selection }, set: { repo.select($0) })) {
            if repo.showsWorkingRow, let st = repo.status {
                WorkingRow(status: st)
                    .frame(height: Self.rowHeight)
                    .listRowInsets(EdgeInsets(top: 0, leading: 4, bottom: 0, trailing: 8))
                    .listRowSeparator(.hidden)
                    .tag(workingCopyID)
            }
            ForEach(Array(repo.commits.enumerated()), id: \.element.sha) { i, c in
                CommitRow(commit: c, graph: i < repo.graph.count ? repo.graph[i] : nil,
                          lanes: repo.maxLanes, showGraph: repo.searchText.isEmpty)
                    .frame(height: Self.rowHeight)
                    .listRowInsets(EdgeInsets(top: 0, leading: 4, bottom: 0, trailing: 8))
                    .listRowSeparator(.hidden)
                    .tag(c.sha)
                    .contextMenu {
                        Button("Copy Commit Hash") { Workspace.copy(c.sha) }
                        Button("Copy Subject") { Workspace.copy(c.subject) }
                        Divider()
                        Button("New Branch Here…") {
                            repo.select(c.sha)
                            NotificationCenter.default.post(name: .sporkNewBranch, object: nil)
                        }
                    }
            }
            if !repo.exhausted {
                HStack { Spacer(); ProgressView().controlSize(.small); Spacer() }
                    .onAppear { repo.loadMore() }
            }
        }
        .listStyle(.plain)
        .environment(\.defaultMinListRowHeight, Self.rowHeight)
    }
}

struct RemoteStrip: View {
    @Bindable var repo: RepoModel

    var body: some View {
        let ctx = repo.fetchContext
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(repo.remoteStates) { st in
                    Menu {
                        Button("Fetch \(st.remote)") { repo.fetch(st.remote) }
                        Button("Pull from \(st.remote)") { repo.pull(st.remote) }
                        Button("Push to \(st.remote)") { repo.push([st.remote]) }
                        Divider()
                        Button("Copy URL") {
                            if let r = repo.remotes.first(where: { $0.name == st.remote }) { Workspace.copy(r.fetchURL) }
                        }
                    } label: {
                        chip(st, offline: ctx.offline.contains(st.remote), error: ctx.errors[st.remote])
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
        }
    }

    func chip(_ st: RemoteBranchState, offline: Bool, error: String?) -> some View {
        HStack(spacing: 4) {
            Image(systemName: offline ? "wifi.slash" : (error != nil ? "exclamationmark.triangle" : "server.rack"))
                .imageScale(.small)
            Text(st.remote).font(.caption.weight(.semibold))
            if offline {
                Text("offline").font(.caption).foregroundStyle(.secondary)
            } else if !st.exists {
                Text(repo.branch.map { "no \($0)" } ?? "—").font(.caption).foregroundStyle(.secondary)
            } else if st.ahead == 0 && st.behind == 0 {
                Image(systemName: "checkmark").imageScale(.small).foregroundStyle(.green)
            } else {
                if st.ahead > 0 { Text("↑\(st.ahead)").font(.caption.monospacedDigit()).foregroundStyle(.orange) }
                if st.behind > 0 { Text("↓\(st.behind)").font(.caption.monospacedDigit()).foregroundStyle(.orange) }
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(.quaternary.opacity(0.6), in: Capsule())
        .help(chipHelp(st, offline: offline, error: error))
    }

    func chipHelp(_ st: RemoteBranchState, offline: Bool, error: String?) -> String {
        if offline { return "\(st.remote): server offline (showing the last fetched state)" }
        if let error { return "\(st.remote): last fetch failed\n\(error.prefix(300))" }
        if !st.exists { return "\(st.remote) has no branch named \(repo.branch ?? "")" }
        var lines = [st.remote]
        if st.ahead > 0 { lines.append(Workspace.plural(st.ahead, "commit") + " to push") }
        if st.behind > 0 { lines.append(Workspace.plural(st.behind, "commit") + " to pull") }
        if st.ahead == 0 && st.behind == 0 { lines.append("In sync") }
        return lines.joined(separator: "\n")
    }
}

struct WorkingRow: View {
    let status: WorkingStatus

    var body: some View {
        HStack(spacing: 6) {
            Canvas { ctx, size in
                let x = 10.0
                let mid = size.height / 2
                var line = Path()
                line.move(to: CGPoint(x: x, y: mid))
                line.addLine(to: CGPoint(x: x, y: size.height))
                ctx.stroke(line, with: .color(GraphPalette.color(0)), style: StrokeStyle(lineWidth: 2, dash: [3, 3]))
                let dot = Path(ellipseIn: CGRect(x: x - 4, y: mid - 4, width: 8, height: 8))
                ctx.stroke(dot, with: .color(GraphPalette.color(0)), lineWidth: 2)
            }
            .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text("Uncommitted Changes").italic().lineLimit(1)
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 0)
        }
    }

    var detail: String {
        var parts = [Workspace.plural(status.entries.count, "file")]
        if !status.staged.isEmpty { parts.append("\(status.staged.count) staged") }
        if !status.conflicted.isEmpty { parts.append("\(status.conflicted.count) conflicted") }
        return parts.joined(separator: " · ")
    }
}

struct CommitRow: View {
    let commit: Commit
    let graph: GraphRow?
    let lanes: Int
    let showGraph: Bool

    var body: some View {
        HStack(spacing: 6) {
            if showGraph, let graph {
                GraphCell(row: graph, lanes: lanes)
                    .frame(width: CGFloat(lanes) * GraphCell.laneWidth + 6)
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 4) {
                    ForEach(Parsers.parseRefs(commit.refs).prefix(4), id: \.self) { RefPill(label: $0) }
                    Text(commit.subject.isEmpty ? "(no message)" : commit.subject)
                        .fontWeight(.medium)
                        .lineLimit(1)
                }
                Text("\(commit.authorName) · \(Workspace.relative(commit.authorTime)) · \(commit.shortSHA)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
    }
}

enum GraphPalette {
    static let colors: [Color] = [.blue, .green, .orange, .purple, .red, .brown, .teal, .yellow]
    static func color(_ i: Int) -> Color { colors[i % colors.count] }
}

struct GraphCell: View {
    let row: GraphRow
    let lanes: Int
    static let laneWidth: CGFloat = 14

    var body: some View {
        Canvas { ctx, size in
            let mid = size.height / 2
            func x(_ lane: Int) -> CGFloat { 3 + CGFloat(min(lane, lanes - 1)) * Self.laneWidth + Self.laneWidth / 2 }
            func seg(_ s: GraphRow.Segment, _ y0: CGFloat, _ y1: CGFloat) {
                var p = Path()
                let x0 = x(s.from), x1 = x(s.to)
                p.move(to: CGPoint(x: x0, y: y0))
                if x0 == x1 {
                    p.addLine(to: CGPoint(x: x1, y: y1))
                } else {
                    let ym = (y0 + y1) / 2
                    p.addCurve(to: CGPoint(x: x1, y: y1), control1: CGPoint(x: x0, y: ym), control2: CGPoint(x: x1, y: ym))
                }
                ctx.stroke(p, with: .color(GraphPalette.color(s.color)), lineWidth: 2)
            }
            for s in row.top { seg(s, 0, mid) }
            for s in row.bottom { seg(s, mid, size.height) }
            let cx = x(row.node)
            ctx.fill(Path(ellipseIn: CGRect(x: cx - 4, y: mid - 4, width: 8, height: 8)),
                     with: .color(GraphPalette.color(row.color)))
        }
    }
}
