import SporkCore
import SwiftUI

struct ChangesView: View {
    @Bindable var repo: RepoModel
    @AppStorage("changesListWidth") private var listWidth = 280.0
    @AppStorage("wrapDiffLines") private var wrap = false

    var body: some View {
        ResizableHSplit(leadingWidth: $listWidth, minLeading: 200, minTrailing: 320) {
            fileList
        } trailing: {
            VStack(spacing: 0) {
                HStack {
                    Text(repo.selectedChange?.path ?? "")
                        .font(.caption).foregroundStyle(.secondary)
                        .lineLimit(1).truncationMode(.head)
                    Spacer()
                    Toggle(isOn: $wrap) { Image(systemName: "text.word.spacing") }
                        .toggleStyle(.button)
                        .buttonStyle(.borderless)
                        .help("Wrap Long Lines")
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                Divider()
                diffPane
            }
        }
    }

    // MARK: file list

    var selectionBinding: Binding<ChangeKey?> {
        Binding(guarded: { repo.selectedChange }, set: { repo.selectedChange = $0; repo.loadDiff() })
    }

    @ViewBuilder var fileList: some View {
        if repo.selection == workingCopyID, let st = repo.status {
            List(selection: selectionBinding) {
                if !st.conflicted.isEmpty {
                    Section("Conflicts (\(st.conflicted.count))") {
                        ForEach(st.conflicted) { e in
                            workingRow(e, code: "U", staged: false)
                        }
                    }
                }
                Section {
                    ForEach(st.staged) { e in
                        workingRow(e, code: e.index == "." ? "M" : e.index, staged: true)
                    }
                } header: {
                    HStack {
                        Text("Staged (\(st.staged.count))")
                        Spacer()
                        if !st.staged.isEmpty {
                            Button("Unstage All") {
                                repo.unstage(st.staged.flatMap { [$0.oldPath, $0.path].compactMap { $0 } })
                            }
                            .buttonStyle(.borderless).font(.caption)
                        }
                    }
                }
                let others = st.unstaged.filter { !$0.conflicted }
                Section {
                    ForEach(others) { e in
                        workingRow(e, code: e.untracked ? "?" : (e.worktree == "." ? "M" : e.worktree), staged: false)
                    }
                } header: {
                    HStack {
                        Text("Changes (\(others.count))")
                        Spacer()
                        if !others.isEmpty {
                            Button("Stage All") { repo.stage(others.map(\.path)) }
                                .buttonStyle(.borderless).font(.caption)
                        }
                    }
                }
            }
            .listStyle(.sidebar)
        } else {
            let groups = Dictionary(grouping: repo.changes) { ($0.path as NSString).deletingLastPathComponent }
            List(selection: selectionBinding) {
                ForEach(groups.keys.sorted(), id: \.self) { dir in
                    Section(dir.isEmpty ? "/" : dir) {
                        ForEach(groups[dir]!.sorted { $0.path.localizedStandardCompare($1.path) == .orderedAscending }) { c in
                            HStack(spacing: 6) {
                                ChangeLetter(code: c.status)
                                Text(displayName(c)).lineLimit(1).truncationMode(.middle)
                                Spacer(minLength: 4)
                                if let a = c.additions, a > 0 { Text("+\(a)").font(.caption.monospacedDigit()).foregroundStyle(.green) }
                                if let d = c.deletions, d > 0 { Text("−\(d)").font(.caption.monospacedDigit()).foregroundStyle(.red) }
                            }
                            .help(c.displayPath)
                            .tag(ChangeKey(path: c.path, staged: false))
                            .contextMenu { fileMenu(c.path, working: false, staged: false) }
                        }
                    }
                }
            }
            .listStyle(.sidebar)
            .overlay {
                if repo.changesFor == repo.selection && repo.changes.isEmpty {
                    emptyState("No Changes", "checkmark.circle", "This commit doesn't change any files.")
                }
            }
        }
    }

    func displayName(_ c: FileChange) -> String {
        let name = (c.path as NSString).lastPathComponent
        if let old = c.oldPath, old != c.path { return "\((old as NSString).lastPathComponent) → \(name)" }
        return name
    }

    func workingRow(_ e: StatusEntry, code: String, staged: Bool) -> some View {
        HStack(spacing: 6) {
            Toggle("", isOn: Binding(guarded: { staged }, set: { on in
                if on { repo.stage([e.path]) } else { repo.unstage([e.oldPath, e.path].compactMap { $0 }) }
            }))
            .toggleStyle(.checkbox)
            .labelsHidden()
            .help(staged ? "Unstage" : "Stage")
            ChangeLetter(code: code)
            Text(e.oldPath.map { "\($0) → \(e.path)" } ?? e.path)
                .lineLimit(1).truncationMode(.middle)
        }
        .help(e.path)
        .tag(ChangeKey(path: e.path, staged: staged))
        .contextMenu { fileMenu(e.path, working: true, staged: staged) }
    }

    @ViewBuilder func fileMenu(_ path: String, working: Bool, staged: Bool) -> some View {
        if working {
            if staged {
                Button("Unstage") { repo.unstage([path]) }
            } else {
                Button("Stage") { repo.stage([path]) }
                Button("Discard Changes…") { repo.discard([path]) }
            }
            Divider()
        }
        let full = (repo.path as NSString).appendingPathComponent(path)
        if FileManager.default.fileExists(atPath: full) {
            Button("Open") { Workspace.open(full, withApp: repo.app.settings.editor) }
            Button("Show in Finder") { Workspace.reveal(full) }
        }
        Button("Copy Path") { Workspace.copy(full) }
        Button("File History") { repo.showHistory(of: path) }
    }

    // MARK: diff

    @ViewBuilder var diffPane: some View {
        if repo.selectedChange == nil {
            emptyState("No File Selected", "doc.text", "Select a file to see its changes.")
        } else if let key = repo.selectedChange {
            let entry = repo.status?.entries.first { $0.path == key.path }
            let working = repo.selection == workingCopyID
            DiffView(diffs: repo.diffs, wrap: wrap, actions: working ? actions(staged: key.staged,
                                                                                 untracked: entry?.untracked ?? false,
                                                                                 conflicted: entry?.conflicted ?? false) : [])
        }
    }

    func actions(staged: Bool, untracked: Bool, conflicted: Bool) -> [HunkAction] {
        if conflicted || untracked { return [] }
        if staged { return [HunkAction(title: "Unstage Hunk", destructive: false) { repo.unstageHunk($0, $1) }] }
        return [HunkAction(title: "Stage Hunk", destructive: false) { repo.stageHunk($0, $1) },
                HunkAction(title: "Discard…", destructive: true) { repo.discardHunk($0, $1) }]
    }
}

struct HunkAction: Identifiable {
    let id = UUID()
    let title: String
    let destructive: Bool
    let perform: (FileDiff, Int) -> Void
}

/// Unified diff with line numbers. Plain SwiftUI text in a lazy stack: no
/// wrapped NSTextView, whose sizing fights SwiftUI's (Cursed Knowledge).
struct DiffView: View {
    let diffs: [FileDiff]
    let wrap: Bool
    let actions: [HunkAction]
    @State private var showHuge = false
    static let maxLines = 20_000

    var body: some View {
        let total = diffs.reduce(0) { $0 + $1.hunks.reduce(0) { $0 + $1.lines.count } }
        if diffs.isEmpty {
            emptyState("No Textual Changes", "doc.text", "Only file metadata changed, or the file is empty.")
        } else if total > Self.maxLines && !showHuge {
            ContentUnavailableView {
                Label("Large Diff", systemImage: "exclamationmark.triangle")
            } description: {
                Text("This diff has \(total) lines.")
            } actions: {
                Button("Show Anyway") { showHuge = true }
            }
        } else {
            let width = diffs.flatMap(\.hunks).map { max($0.oldStart + $0.oldCount, $0.newStart + $0.newCount) }
                .max().map { String($0).count } ?? 1
            ScrollView(wrap ? .vertical : [.vertical, .horizontal]) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(diffs.enumerated()), id: \.offset) { _, fd in
                        if fd.binary {
                            Text("Binary file — contents not shown").italic().foregroundStyle(.secondary).padding(8)
                        } else if fd.hunks.isEmpty {
                            Text("No textual changes").italic().foregroundStyle(.secondary).padding(8)
                        }
                        ForEach(Array(fd.hunks.enumerated()), id: \.offset) { hi, h in
                            HunkHeader(header: h.header, actions: actions) { a in a.perform(fd, hi) }
                            ForEach(Array(h.lines.enumerated()), id: \.offset) { _, line in
                                DiffLineView(line: line, numberWidth: width, wrap: wrap)
                            }
                        }
                    }
                }
                .font(.system(size: 12, design: .monospaced))
                .textSelection(.enabled)
                .padding(.bottom, 24)
            }
        }
    }
}

struct HunkHeader: View {
    let header: String
    let actions: [HunkAction]
    let perform: (HunkAction) -> Void

    var body: some View {
        HStack(spacing: 6) {
            ForEach(actions) { a in
                Button(a.title) { perform(a) }
                    .controlSize(.small)
                    .tint(a.destructive ? .red : nil)
            }
            Text(header).foregroundStyle(.secondary).lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.accentColor.opacity(0.10))
        .padding(.top, 6)
    }
}

struct DiffLineView: View {
    let line: DiffLine
    let numberWidth: Int
    let wrap: Bool

    var body: some View {
        if line.kind == "\\" {
            Text(String(repeating: " ", count: numberWidth * 2 + 3) + "↵ " + line.text.trimmingCharacters(in: .whitespaces))
                .italic().foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        } else {
            HStack(alignment: .top, spacing: 0) {
                Text(pad(line.oldNo) + " " + pad(line.newNo) + " ")
                    .foregroundStyle(.tertiary)
                Text(sign + " " + line.text)
                    .lineLimit(wrap ? nil : 1)
                    .fixedSize(horizontal: !wrap, vertical: false)
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(background)
        }
    }

    var sign: String { line.kind == "+" ? "+" : (line.kind == "-" ? "−" : " ") }

    var background: Color {
        switch line.kind {
        case "+": return Color.green.opacity(0.14)
        case "-": return Color.red.opacity(0.13)
        default: return .clear
        }
    }

    func pad(_ n: Int?) -> String {
        let s = n.map(String.init) ?? ""
        return String(repeating: " ", count: max(0, numberWidth - s.count)) + s
    }
}
