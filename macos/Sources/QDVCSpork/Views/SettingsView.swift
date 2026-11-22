import AppKit
import SporkCore
import SwiftUI
import UniformTypeIdentifiers

struct SettingsView: View {
    @Bindable var model: AppModel

    var body: some View {
        TabView {
            GeneralSettings(model: model)
                .tabItem { Label("General", systemImage: "gearshape") }
            ServersSettings(model: model)
                .tabItem { Label("Servers", systemImage: "lock.shield") }
            CommandLineSettings()
                .tabItem { Label("Command Line", systemImage: "terminal") }
        }
        .frame(width: 600, height: 460)
    }
}

struct GeneralSettings: View {
    @Bindable var model: AppModel
    @State private var gitVersion = "…"

    var body: some View {
        Form {
            Section {
                appPicker("Editor", value: model.settings.editor, placeholder: "Default app for each file") {
                    model.settings.editor = $0; model.save()
                }
                appPicker("Terminal", value: model.settings.terminal, placeholder: "Terminal") {
                    model.settings.terminal = $0; model.save()
                }
            } header: {
                Text("Tools")
            } footer: {
                Text("Used by Open and Open in Terminal.")
            }
            Section("Network") {
                Stepper(value: Binding(guarded: { model.settings.fetchTimeout },
                                       set: { model.settings.fetchTimeout = $0; model.save() }),
                        in: 10...3600, step: 10) {
                    LabeledContent("Timeout", value: "\(model.settings.fetchTimeout) s")
                }
            }
            Section("Git") {
                LabeledContent("Version", value: gitVersion)
                LabeledContent("Location", value: model.runner.gitPath)
            }
        }
        .formStyle(.grouped)
        .task {
            let runner = model.runner
            let v = try? await offMain { runner.version() }
            gitVersion = v ?? "git not found"
        }
    }

    func appPicker(_ title: String, value: String, placeholder: String,
                   set: @escaping (String) -> Void) -> some View {
        LabeledContent(title) {
            HStack {
                Text(value.isEmpty ? placeholder : (value as NSString).lastPathComponent)
                    .foregroundStyle(value.isEmpty ? .secondary : .primary)
                Button("Choose…") {
                    let panel = NSOpenPanel()
                    panel.allowedContentTypes = [.application]
                    panel.directoryURL = URL(fileURLWithPath: "/Applications")
                    if panel.runModal() == .OK, let url = panel.url { set(url.path) }
                }
                if !value.isEmpty {
                    Button("Reset") { set("") }
                }
            }
        }
    }
}

struct ServersSettings: View {
    @Bindable var model: AppModel
    @State private var pending: PendingCert?
    @State private var testing: String?

    struct PendingCert: Identifiable {
        let id = UUID()
        let source: URL
        let info: CertInfo
        var suggestions: [Certificates.URLSuggestion]
        var chosen: Set<String>
        var custom = ""
    }

    var body: some View {
        Form {
            Section {
                Text("Add a local git server's cert.pem (never key.pem) and Spork trusts it for that server's HTTPS URL. Spork passes the certificate to git itself; your ~/.gitconfig and keychain are not changed.")
                    .font(.callout).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Section {
                if model.settings.servers.isEmpty {
                    Text("No trusted servers").foregroundStyle(.secondary)
                }
                ForEach(model.settings.servers) { v in serverRow(v) }
            } header: {
                HStack {
                    Text("Trusted Servers")
                    Spacer()
                    Button("Add Certificate…") { chooseCertificate() }
                }
            }
        }
        .formStyle(.grouped)
        .sheet(item: $pending) { p in
            TrustSheet(pending: p, onCancel: { pending = nil }, onTrust: { urls in
                for u in urls { try? model.settings.addServer(url: u, certificateAt: p.source) }
                model.save()
                pending = nil
                if !urls.isEmpty { model.show("Trusted for \(urls.joined(separator: ", "))") }
            })
        }
    }

    func serverRow(_ v: TrustedServerEntry) -> some View {
        let info = FileManager.default.contents(atPath: v.certificate).flatMap { try? Certificates.load($0).first }
        let days = info?.daysLeft() ?? -1
        return HStack(spacing: 10) {
            Image(systemName: info == nil || days < 30 ? "exclamationmark.triangle.fill" : "lock.fill")
                .foregroundStyle(info == nil || days < 30 ? .orange : .green)
            VStack(alignment: .leading, spacing: 2) {
                Text(v.url)
                if let info {
                    Text("\(info.names.joined(separator: ", ")) · " +
                         (days < 0 ? "expired" : "expires \(info.notAfter.formatted(date: .abbreviated, time: .omitted)) (\(days) days)"))
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Can't read \(v.certificate)").font(.caption).foregroundStyle(.red)
                }
            }
            Spacer()
            if testing == v.url {
                ProgressView().controlSize(.small)
            } else {
                Button("Test") { test(v.url) }
                    .help("Contact a bookmarked remote on this server using this certificate")
            }
            Button {
                model.alert = AppAlert(title: "Stop Trusting This Server?",
                                       message: "Spork will no longer accept the certificate for \(v.url).",
                                       actions: [("Remove", { model.settings.removeServer(v.url); model.save() })])
            } label: { Image(systemName: "trash") }
                .buttonStyle(.borderless)
                .help("Stop Trusting")
        }
    }

    func chooseCertificate() {
        let panel = NSOpenPanel()
        panel.message = "Choose the server's cert.pem"
        let extra: [UTType] = ["pem", "crt", "cer"].compactMap { UTType(filenameExtension: $0) }
        panel.allowedContentTypes = extra + [UTType.x509Certificate]
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let data = try Data(contentsOf: url)
            guard let info = try Certificates.load(data).first else { return }
            Task {
                let suggestions = Certificates.suggestServerURLs(info, remoteURLs: await model.remoteURLsByLabel())
                pending = PendingCert(source: url, info: info, suggestions: suggestions,
                                      chosen: Set(suggestions.prefix(1).map(\.url)))
            }
        } catch {
            model.alert = AppAlert(title: "Not a Usable Certificate", error: error)
        }
    }

    func test(_ url: String) {
        let runner = model.runner
        let bookmarks = model.settings.bookmarks
        testing = url
        Task {
            do {
                let label: String? = try await offMain {
                    for b in bookmarks {
                        let repo = Repository(runner: runner, path: b.path)
                        for r in (try? repo.remotes()) ?? [] where Certificates.server(for: r.fetchURL, in: [(url, "")]) != nil {
                            try repo.lsRemoteCheck(r.name)
                            return "\(b.displayName): \(r.name)"
                        }
                    }
                    return nil
                }
                testing = nil
                model.show(label.map { "Connected: \($0)" } ?? "No bookmarked repository has a remote on this server")
            } catch {
                testing = nil
                model.alert = AppAlert(title: "Couldn't Reach \(url)", error: error)
            }
        }
    }
}

struct TrustSheet: View {
    @State var pending: ServersSettings.PendingCert
    let onCancel: () -> Void
    let onTrust: ([String]) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Trust This Certificate?").font(.title3.weight(.semibold))
            Text("From \(pending.source.lastPathComponent)").foregroundStyle(.secondary)
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 4) {
                GridRow { Text("Covers").foregroundStyle(.secondary); Text(pending.info.names.joined(separator: ", ")).textSelection(.enabled) }
                GridRow {
                    Text("Expires").foregroundStyle(.secondary)
                    Text(pending.info.notAfter.formatted(date: .long, time: .omitted) + " (\(pending.info.daysLeft()) days)")
                }
                GridRow {
                    Text("SHA-256").foregroundStyle(.secondary)
                    Text(pending.info.sha256).font(.caption.monospaced()).textSelection(.enabled).lineLimit(2)
                }
            }
            if pending.info.isCA {
                Text("This is a CA certificate. That's fine for git, though Forgejo's own cert.pem normally isn't one.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Text("Trust it for:").font(.headline)
            ForEach(pending.suggestions, id: \.url) { s in
                Toggle(isOn: Binding(get: { pending.chosen.contains(s.url) }, set: { on in
                    if on { pending.chosen.insert(s.url) } else { pending.chosen.remove(s.url) }
                })) {
                    VStack(alignment: .leading) {
                        Text(s.url)
                        if !s.remotes.isEmpty {
                            Text("Used by " + s.remotes.prefix(3).joined(separator: ", ") + (s.remotes.count > 3 ? "…" : ""))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                .toggleStyle(.checkbox)
            }
            TextField("Another URL, e.g. https://myserver.local:3000/", text: $pending.custom)
            Text("Include the port: git matches the server URL exactly.").font(.caption).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button("Cancel", role: .cancel, action: onCancel).keyboardShortcut(.cancelAction)
                Button("Trust") {
                    var urls = Array(pending.chosen).sorted()
                    let extra = pending.custom.trimmingCharacters(in: .whitespaces)
                    if !extra.isEmpty { urls.append(Endpoint.normaliseServerURL(extra)) }
                    onTrust(urls.filter { Endpoint.parse($0).scheme == "https" })
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(width: 480)
    }
}

struct CommandLineSettings: View {
    @State private var installedAt: String?
    @State private var error: String?

    var body: some View {
        Form {
            Section {
                Text("Run spork . in Terminal to open the repository you're in.")
                LabeledContent("Install to") { Text(Workspace.cliDestination.path).textSelection(.enabled) }
                HStack {
                    Spacer()
                    Button(FileManager.default.fileExists(atPath: Workspace.cliDestination.path) ? "Reinstall" : "Install") {
                        do {
                            installedAt = try Workspace.installCLI().path
                            error = nil
                        } catch let e {
                            error = e.localizedDescription
                        }
                    }
                    .buttonStyle(.borderedProminent)
                }
                if let installedAt { Text("Installed \(installedAt)").foregroundStyle(.green) }
                if let error { Text(error).foregroundStyle(.red) }
            } header: {
                Text("The spork Command")
            } footer: {
                if Workspace.cliDestination.path.contains("/.local/bin") {
                    Text("Add ~/.local/bin to your PATH, e.g. echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.zshrc")
                }
            }
        }
        .formStyle(.grouped)
    }
}
