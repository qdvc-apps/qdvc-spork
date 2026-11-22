import SporkCore
import SwiftUI

struct DetailView: View {
    @Bindable var repo: RepoModel

    var body: some View {
        VStack(spacing: 0) {
            Picker("View", selection: Binding(guarded: { repo.tab }, set: { repo.tab = $0; repo.loadTab() })) {
                ForEach(DetailTab.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .frame(maxWidth: 320)
            .padding(.vertical, 8)
            Divider()
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    @ViewBuilder var content: some View {
        if repo.selection == nil {
            emptyState("No Commit Selected", "doc.text.magnifyingglass")
        } else {
            switch repo.tab {
            case .commit:
                if repo.selection == workingCopyID {
                    ComposerView(repo: repo)
                } else if let c = repo.detail, c.sha == repo.selection {
                    CommitInfoView(repo: repo, commit: c)
                } else {
                    ProgressView()
                }
            case .changes:
                ChangesView(repo: repo)
            case .browse:
                BrowseView(repo: repo)
            }
        }
    }
}

struct CommitInfoView: View {
    @Bindable var repo: RepoModel
    let commit: Commit

    var body: some View {
        Form {
            Section {
                VStack(alignment: .leading, spacing: 10) {
                    let refs = Parsers.parseRefs(commit.refs)
                    if !refs.isEmpty {
                        HStack(spacing: 4) { ForEach(refs, id: \.self) { RefPill(label: $0) } }
                    }
                    Text(commit.subject.isEmpty ? "(no message)" : commit.subject)
                        .font(.title2.weight(.semibold))
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                    if !commit.body.isEmpty {
                        Text(commit.body)
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.vertical, 4)
            }
            Section("Details") {
                LabeledContent("Commit") {
                    HStack {
                        Text(commit.sha).font(.system(.body, design: .monospaced)).textSelection(.enabled)
                            .lineLimit(1).truncationMode(.middle)
                        Button { Workspace.copy(commit.sha) } label: { Image(systemName: "doc.on.doc") }
                            .buttonStyle(.borderless)
                            .help("Copy Commit Hash")
                    }
                }
                LabeledContent("Author") {
                    VStack(alignment: .trailing, spacing: 2) {
                        Text("\(commit.authorName) <\(commit.authorEmail)>").textSelection(.enabled)
                        Text("\(Workspace.fullDate(commit.authorTime)) (\(Workspace.relative(commit.authorTime)))")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                if commit.committerName != commit.authorName || commit.committerEmail != commit.authorEmail
                    || abs(commit.committerTime - commit.authorTime) > 60 {
                    LabeledContent("Committer") {
                        VStack(alignment: .trailing, spacing: 2) {
                            Text("\(commit.committerName) <\(commit.committerEmail)>").textSelection(.enabled)
                            Text(Workspace.fullDate(commit.committerTime)).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if !commit.parents.isEmpty {
                Section {
                    ForEach(commit.parents, id: \.self) { p in
                        Button {
                            repo.openCommit(p)
                        } label: {
                            HStack {
                                Text(String(p.prefix(12))).font(.system(.body, design: .monospaced))
                                Spacer()
                                Image(systemName: "chevron.right").foregroundStyle(.secondary)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                } header: {
                    Text(commit.parents.count > 1 ? "Parents" : "Parent")
                } footer: {
                    if commit.parents.count > 1 { Text("Changes are shown against the first parent.") }
                }
            }
            if repo.changesFor == commit.sha {
                Section("Changes") {
                    let adds = repo.changes.reduce(0) { $0 + ($1.additions ?? 0) }
                    let dels = repo.changes.reduce(0) { $0 + ($1.deletions ?? 0) }
                    LabeledContent(Workspace.plural(repo.changes.count, "file") + " changed") {
                        HStack(spacing: 6) {
                            Text("+\(adds)").foregroundStyle(.green)
                            Text("−\(dels)").foregroundStyle(.red)
                        }
                        .font(.body.monospacedDigit())
                    }
                    Button("Show Changes") { repo.tab = .changes; repo.loadTab() }
                }
            }
        }
        .formStyle(.grouped)
    }
}

struct ComposerView: View {
    @Bindable var repo: RepoModel
    @FocusState private var summaryFocused: Bool

    var body: some View {
        let st = repo.status
        let staged = st?.staged.count ?? 0
        VStack(spacing: 0) {
            Form {
                Section {
                    TextField("Summary", text: $repo.summary, prompt: Text("Summary (required)"))
                        .focused($summaryFocused)
                        .onSubmit { repo.commit() }
                    ZStack(alignment: .topLeading) {
                        if repo.message.isEmpty {
                            Text("Description").foregroundStyle(.tertiary).padding(.top, 1).padding(.leading, 5)
                        }
                        TextEditor(text: $repo.message)
                            .font(.body)
                            .frame(minHeight: 120)
                            .scrollContentBackground(.hidden)
                    }
                } header: {
                    Text("Commit")
                } footer: {
                    HStack {
                        Text(caption(staged: staged, status: st))
                        Spacer()
                        if repo.summary.count > 0 {
                            Text("\(repo.summary.count)")
                                .monospacedDigit()
                                .foregroundStyle(repo.summary.count > 72 ? Color.orange : Color.secondary)
                                .help("Summaries over 72 characters get cut off in many tools")
                        }
                    }
                }
                Section {
                    Toggle("Amend Last Commit", isOn: Binding(guarded: { repo.amend }, set: { repo.setAmend($0) }))
                } footer: {
                    Text("Replaces the previous commit instead of adding a new one.")
                }
            }
            .formStyle(.grouped)
            Divider()
            HStack {
                Text(repo.identity.isEmpty ? "Set user.name and user.email in git to commit"
                     : "Committing as \(repo.identity)")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                Spacer()
                Button(repo.amend ? "Amend Commit" : (staged > 0 ? "Commit \(Workspace.plural(staged, "File"))" : "Commit")) {
                    repo.commit()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.return, modifiers: .command)
                .disabled(!repo.canCommit)
            }
            .padding(12)
        }
        .onAppear { summaryFocused = true }
    }

    func caption(staged: Int, status: WorkingStatus?) -> String {
        let where_ = repo.branch.map { "on \($0)" } ?? "with a detached HEAD"
        if let st = status, !st.conflicted.isEmpty { return "Resolve conflicts before committing." }
        if staged > 0 { return "\(Workspace.plural(staged, "staged file")) will be committed \(where_)." }
        return "Nothing is staged yet. Tick files in Changes to stage them (\(where_))."
    }
}
