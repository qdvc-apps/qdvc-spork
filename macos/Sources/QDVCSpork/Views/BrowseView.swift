import AppKit
import SporkCore
import SwiftUI

/// A flattened, expandable file tree (rather than OutlineGroup) so Spork can
/// expand folders down to a file when you jump to its history.
struct TreeRow: Identifiable, Hashable {
    var id: String { path }
    let path: String
    let name: String
    let depth: Int
    let isDir: Bool
    let kind: String
}

func visibleTreeRows(_ entries: [TreeEntry], expanded: Set<String>) -> [TreeRow] {
    final class Node {
        var children: [String: Node] = [:]
        var entry: TreeEntry?
    }
    let root = Node()
    for e in entries {
        var node = root
        for part in e.path.split(separator: "/").map(String.init) {
            if node.children[part] == nil { node.children[part] = Node() }
            node = node.children[part]!
        }
        node.entry = e
    }
    var out: [TreeRow] = []
    func walk(_ node: Node, _ prefix: String, _ depth: Int) {
        let sorted = node.children.sorted { a, b in
            let ad = a.value.entry == nil, bd = b.value.entry == nil
            if ad != bd { return ad }
            return a.key.localizedStandardCompare(b.key) == .orderedAscending
        }
        for (name, child) in sorted {
            let path = prefix.isEmpty ? name : prefix + "/" + name
            let isDir = child.entry == nil
            out.append(TreeRow(path: path, name: name, depth: depth, isDir: isDir, kind: child.entry?.kind ?? "tree"))
            if isDir && expanded.contains(path) { walk(child, path, depth + 1) }
        }
    }
    walk(root, "", 0)
    return out
}

struct BrowseView: View {
    @Bindable var repo: RepoModel
    @AppStorage("browseListWidth") private var listWidth = 280.0

    var body: some View {
        ResizableHSplit(leadingWidth: $listWidth, minLeading: 200, minTrailing: 320) {
            tree
        } trailing: {
            VStack(spacing: 0) {
                header
                Divider()
                content.frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    var tree: some View {
        let rows = visibleTreeRows(repo.tree, expanded: repo.expanded)
        return List(selection: Binding(guarded: { repo.browsePath }, set: { p in
            if let p, let row = rows.first(where: { $0.path == p }), row.isDir {
                toggle(p)
            } else {
                repo.openFile(p)
            }
        })) {
            ForEach(rows) { row in
                HStack(spacing: 4) {
                    if row.isDir {
                        Image(systemName: repo.expanded.contains(row.path) ? "chevron.down" : "chevron.right")
                            .font(.caption2).foregroundStyle(.secondary).frame(width: 10)
                            .onTapGesture { toggle(row.path) }
                    } else {
                        Spacer().frame(width: 10)
                    }
                    Image(systemName: row.isDir ? "folder" : (row.kind == "commit" ? "shippingbox" : "doc"))
                        .foregroundStyle(row.isDir ? Color.accentColor : .secondary)
                    Text(row.name).lineLimit(1)
                }
                .padding(.leading, CGFloat(row.depth) * 14)
                .tag(row.path)
            }
        }
        .listStyle(.sidebar)
        .overlay {
            if repo.tree.isEmpty {
                emptyState("No Files", "folder", repo.treeRev == nil ? "There are no commits yet." : "")
            }
        }
    }

    func toggle(_ path: String) {
        if repo.expanded.contains(path) { repo.expanded.remove(path) } else { repo.expanded.insert(path) }
    }

    var header: some View {
        HStack(spacing: 8) {
            Text(repo.browsePath ?? "").font(.caption).foregroundStyle(.secondary)
                .lineLimit(1).truncationMode(.head)
            Spacer()
            Picker("Mode", selection: Binding(guarded: { repo.browseMode }, set: { m in
                repo.browseMode = m
                repo.openFile(repo.browsePath)
            })) {
                ForEach(BrowseMode.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(width: 240)
            .disabled(repo.browsePath == nil)
            if let p = repo.browsePath {
                let full = (repo.path as NSString).appendingPathComponent(p)
                Menu {
                    if FileManager.default.fileExists(atPath: full) {
                        Button("Open") { Workspace.open(full, withApp: repo.app.settings.editor) }
                        Button("Show in Finder") { Workspace.reveal(full) }
                    }
                    Button("Copy Path") { Workspace.copy(full) }
                } label: {
                    Image(systemName: "square.and.arrow.up")
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .help("Open")
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
    }

    @ViewBuilder var content: some View {
        if repo.browsePath == nil {
            emptyState("Select a File", "doc.text.magnifyingglass", "Choose a file on the left to see it as of this commit.")
        } else {
            switch repo.browseMode {
            case .contents: blobView
            case .blame: BlameView(lines: repo.blame)
            case .history: historyView
            }
        }
    }

    @ViewBuilder var blobView: some View {
        switch repo.blob {
        case .none:
            ProgressView()
        case .text(let s):
            TextFileView(text: s)
        case .image(let data):
            if let img = NSImage(data: data) {
                ScrollView([.vertical, .horizontal]) {
                    Image(nsImage: img).resizable().scaledToFit()
                        .frame(maxWidth: img.size.width, maxHeight: img.size.height)
                        .padding()
                }
            } else {
                emptyState("Can't Show Image", "photo")
            }
        case .binary(let n):
            emptyState("Binary File", "doc.zipper", ByteCountFormatter.string(fromByteCount: Int64(n), countStyle: .file))
        case .tooLarge(let n):
            emptyState("File Too Large", "exclamationmark.triangle",
                       ByteCountFormatter.string(fromByteCount: Int64(n), countStyle: .file) + " — too big to show here.")
        case .submodule(let sha):
            emptyState("Submodule", "shippingbox", "Points at commit \(sha.prefix(12)).")
        }
    }

    var historyView: some View {
        List(repo.history) { c in
            Button {
                repo.openCommit(c.sha)
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(c.subject.isEmpty ? "(no message)" : c.subject).lineLimit(1)
                    Text("\(c.authorName) · \(Workspace.relative(c.authorTime)) · \(c.shortSHA)")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .listStyle(.inset(alternatesRowBackgrounds: true))
    }
}

struct TextFileView: View {
    let text: String

    var body: some View {
        let lines = Parsers.split(text, on: "\n")
        let shown = lines.last == "" ? Array(lines.dropLast()) : lines
        let width = String(shown.count).count
        ScrollView([.vertical, .horizontal]) {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(Array(shown.enumerated()), id: \.offset) { i, line in
                    HStack(spacing: 0) {
                        Text(String(i + 1).leftPadded(to: width) + "  ").foregroundStyle(.tertiary)
                        Text(line.replacingOccurrences(of: "\t", with: "    "))
                            .fixedSize(horizontal: true, vertical: false)
                    }
                }
            }
            .font(.system(size: 12, design: .monospaced))
            .textSelection(.enabled)
            .padding(8)
        }
    }
}

struct BlameView: View {
    let lines: [BlameLine]

    var body: some View {
        let width = String(lines.count).count
        let groups = groupIndices()
        ScrollView([.vertical, .horizontal]) {
            LazyVStack(alignment: .leading, spacing: 0) {
                ForEach(Array(lines.enumerated()), id: \.offset) { i, l in
                    let first = i == 0 || lines[i - 1].sha != l.sha
                    let shade = groups[i] % 2 == 1
                    HStack(spacing: 0) {
                        Text(first ? gutter(l) : String(repeating: " ", count: 37))
                            .foregroundStyle(.secondary)
                            .help(first ? "\(l.summary)\n\(l.sha)" : "")
                        Text(String(l.lineNo).leftPadded(to: width) + "  ").foregroundStyle(.tertiary)
                        Text(l.text.replacingOccurrences(of: "\t", with: "    "))
                            .fixedSize(horizontal: true, vertical: false)
                        Spacer(minLength: 0)
                    }
                    .background(shade ? Color.secondary.opacity(0.08) : .clear)
                }
            }
            .font(.system(size: 12, design: .monospaced))
            .textSelection(.enabled)
            .padding(8)
        }
    }

    func gutter(_ l: BlameLine) -> String {
        let who = String(l.author.prefix(14)).padding(toLength: 14, withPad: " ", startingAt: 0)
        let when = String(Workspace.relative(l.authorTime).prefix(12)).padding(toLength: 12, withPad: " ", startingAt: 0)
        return "\(l.sha.prefix(8)) \(who) \(when) "
    }

    /// Alternating shade per run of lines from the same commit.
    func groupIndices() -> [Int] {
        var g = 0
        var out: [Int] = []
        out.reserveCapacity(lines.count)
        for (i, l) in lines.enumerated() {
            if i > 0 && lines[i - 1].sha != l.sha { g += 1 }
            out.append(g)
        }
        return out
    }
}

extension String {
    func leftPadded(to n: Int) -> String {
        count >= n ? self : String(repeating: " ", count: n - count) + self
    }
}
