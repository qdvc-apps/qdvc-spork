import SporkCore
import SwiftUI

struct DashboardView: View {
    @Bindable var model: AppModel

    var body: some View {
        Group {
            if model.settings.bookmarks.isEmpty {
                ContentUnavailableView {
                    Label("Nothing to Show Yet", systemImage: "square.grid.2x2")
                } description: {
                    Text("Bookmark repositories to see their health here, or run spork . in Terminal.")
                } actions: {
                    Button("Add Repository…") { chooseRepository(model) }
                        .buttonStyle(.borderedProminent)
                }
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        banners
                        tiles
                        Text("Repositories").font(.title3.weight(.semibold))
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 280), spacing: 16)], spacing: 16) {
                            ForEach(sortedBookmarks) { b in
                                RepoCard(bookmark: b, summary: model.summaries[b.path]) {
                                    model.selection = b.path
                                }
                            }
                        }
                        servers
                    }
                    .padding(24)
                    .frame(maxWidth: 1200)
                    .frame(maxWidth: .infinity)
                }
            }
        }
        .navigationTitle("Dashboard")
        .navigationSubtitle(model.lastFetch.map { "Last fetched \(Workspace.relative(Int($0.finishedAt.timeIntervalSince1970)))" } ?? "")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                if model.fetching { ProgressView().controlSize(.small) }
                Button {
                    model.fetchEverything()
                } label: {
                    Label("Fetch All", systemImage: "arrow.triangle.2.circlepath")
                }
                .help("Check every server, then fetch every reachable remote (⌥⌘T)")
                .disabled(model.fetching || model.settings.bookmarks.isEmpty)
            }
        }
    }

    var sortedBookmarks: [Bookmark] {
        let order: [Level: Int] = [.missing: 0, .attention: 1, .unknown: 2, .ok: 3]
        return model.settings.bookmarks.sorted { a, b in
            let la = model.summaries[a.path].map { order[$0.level] ?? 4 } ?? 4
            let lb = model.summaries[b.path].map { order[$0.level] ?? 4 } ?? 4
            if la != lb { return la < lb }
            return a.displayName.localizedStandardCompare(b.displayName) == .orderedAscending
        }
    }

    var tiles: some View {
        let c = model.counts
        let servers = model.lastFetch?.servers.values.map { $0 } ?? []
        let offline = servers.filter { $0.online == false }.count
        return HStack(spacing: 16) {
            Tile(title: "Repositories", value: "\(c.total)", color: .primary)
            Tile(title: "Clean & in sync", value: "\(c.ok)", color: .green)
            Tile(title: "Need attention", value: "\(c.attention)", color: .orange)
            Tile(title: "Servers offline", value: servers.isEmpty ? "—" : "\(offline)/\(servers.count)",
                 color: offline > 0 ? .red : .primary)
        }
    }

    @ViewBuilder var banners: some View {
        let offline = model.lastFetch?.servers.values.filter { $0.online == false }.count ?? 0
        if offline > 0 {
            Banner(symbol: "wifi.slash", color: .orange,
                   text: "\(Workspace.plural(offline, "server")) didn't answer; their repositories show the last fetched state.")
        }
        ForEach(certificateWarnings, id: \.self) { w in
            Banner(symbol: "lock.trianglebadge.exclamationmark", color: .red, text: w)
        }
    }

    var certificateWarnings: [String] {
        model.settings.servers.compactMap { v in
            guard let data = FileManager.default.contents(atPath: v.certificate),
                  let info = try? Certificates.load(data).first else {
                return "The certificate file for \(v.url) is missing."
            }
            let days = info.daysLeft()
            if days < 0 { return "The certificate for \(v.url) has expired." }
            if days < 30 { return "The certificate for \(v.url) expires in \(Workspace.plural(days, "day"))." }
            return nil
        }
    }

    @ViewBuilder var servers: some View {
        Text("Servers").font(.title3.weight(.semibold))
        VStack(alignment: .leading, spacing: 0) {
            if let report = model.lastFetch, !report.servers.isEmpty {
                let list = report.servers.values.sorted { ($0.online == false ? 0 : 1, $0.key) < ($1.online == false ? 0 : 1, $1.key) }
                ForEach(Array(list.enumerated()), id: \.element.key) { i, s in
                    if i > 0 { Divider() }
                    ServerRow(server: s, trusted: trustedCert(for: s))
                }
            } else {
                Text("Not checked yet. Press Fetch All to check every server.")
                    .foregroundStyle(.secondary)
                    .padding(12)
            }
        }
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        Text("Checked once each before fetching. Online means the port accepted a connection.")
            .font(.caption).foregroundStyle(.secondary)
    }

    func trustedCert(for s: ServerStatus) -> String? {
        guard let port = s.port, !s.isLocal else { return nil }
        let pairs = model.settings.servers.map { ($0.url, $0.certificate) }
        return Certificates.server(for: "https://\(s.host):\(port)/", in: pairs)?.1
    }
}

struct Tile: View {
    let title: String
    let value: String
    let color: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.system(size: 30, weight: .bold, design: .rounded)).monospacedDigit()
                .foregroundStyle(color)
            Text(title).font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct Banner: View {
    let symbol: String
    let color: Color
    let text: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbol).foregroundStyle(color)
            Text(text)
            Spacer()
        }
        .padding(12)
        .background(color.opacity(0.12), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

struct RepoCard: View {
    let bookmark: Bookmark
    let summary: RepoSummary?
    let open: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: open) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    LevelIcon(level: summary?.level)
                    Text(bookmark.displayName).font(.headline).lineLimit(1)
                    Spacer(minLength: 4)
                    if let b = summary?.branch {
                        RefPill(label: RefLabel(name: b, kind: .head))
                    } else if summary?.detached == true {
                        RefPill(label: RefLabel(name: "detached", kind: .detached))
                    }
                }
                Text((bookmark.path as NSString).abbreviatingWithTildeInPath)
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                if let s = summary {
                    if s.remotes.isEmpty {
                        Text("No remotes").font(.caption).foregroundStyle(.secondary)
                    } else {
                        VStack(alignment: .leading, spacing: 3) {
                            ForEach(s.remotes) { st in remoteRow(st, s) }
                        }
                    }
                    if !s.reasons.isEmpty {
                        Text(s.reasons.joined(separator: " · "))
                            .font(.caption).foregroundStyle(.secondary).lineLimit(3)
                    }
                    if let t = s.lastCommitTime {
                        Text("\(Workspace.relative(t)) — \(s.lastCommitSubject ?? "")")
                            .font(.caption).foregroundStyle(.tertiary).lineLimit(1)
                    }
                } else {
                    Text("Checking…").font(.caption).foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(Color.accentColor.opacity(hovering ? 0.6 : 0), lineWidth: 1.5))
            .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .help(summary?.reasons.joined(separator: "\n") ?? bookmark.path)
    }

    func remoteRow(_ st: RemoteBranchState, _ s: RepoSummary) -> some View {
        let offline = s.offlineRemotes.contains(st.remote)
        let failed = s.fetchErrors[st.remote] != nil
        return HStack(spacing: 6) {
            Image(systemName: offline ? "wifi.slash" : (failed ? "exclamationmark.triangle" : "server.rack"))
                .imageScale(.small).foregroundStyle(.secondary)
            Text(st.remote).font(.caption)
            Spacer()
            if offline {
                Text("offline").font(.caption).foregroundStyle(.secondary)
            } else if !st.exists {
                Text(s.branch.map { "no \($0)" } ?? "—").font(.caption).foregroundStyle(.secondary)
            } else if st.ahead == 0 && st.behind == 0 {
                Text("in sync").font(.caption).foregroundStyle(.green)
            } else {
                if st.ahead > 0 { Text("↑\(st.ahead)").font(.caption.monospacedDigit()).foregroundStyle(.orange) }
                if st.behind > 0 { Text("↓\(st.behind)").font(.caption.monospacedDigit()).foregroundStyle(.orange) }
            }
        }
    }
}

struct ServerRow: View {
    let server: ServerStatus
    let trusted: String?

    var detail: String {
        let remotes: String = Workspace.plural(server.remotes.count, "remote") + ": "
            + server.remotes.prefix(6).joined(separator: ", ")
        if server.online == false {
            let why: String = server.error ?? "no answer"
            return "Offline — \(why) · \(remotes)"
        }
        return remotes
    }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: server.online == true ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(server.online == true ? .green : .red)
            VStack(alignment: .leading, spacing: 2) {
                Text(server.displayName)
                Text(detail)
                    .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            }
            Spacer()
            if let trusted {
                Image(systemName: "lock.fill").foregroundStyle(.secondary).help("Trusted certificate: \(trusted)")
            }
        }
        .padding(12)
    }
}
