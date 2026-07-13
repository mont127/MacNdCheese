import SwiftUI

/// The Winetricks App Store — browse and install the full winetricks verb
/// catalog into the active bottle. Search-and-browse only, by design: no
/// recommended/one-click bundle, since someone who opens an app store
/// already has a specific target in mind.
struct WinetricksStoreSheet: View {
    @EnvironmentObject var backend: BackendClient
    @Environment(\.dismiss) private var dismiss
    @StateObject private var runner = WinetricksRunner()

    @State private var allVerbs: [WinetricksVerb] = []
    @State private var isLoadingCatalog = false
    @State private var searchText = ""
    @State private var selectedCategory: String = "all"
    @State private var installedVerbs: Set<String> = []

    private var isAvailable: Bool { backend.winetricksAvailable() }

    private var scopedVerbs: [WinetricksVerb] {
        selectedCategory == "all" ? allVerbs : allVerbs.filter { $0.category == selectedCategory }
    }

    private var filteredVerbs: [WinetricksVerb] {
        guard !searchText.isEmpty else { return scopedVerbs }
        return scopedVerbs.filter {
            $0.title.localizedCaseInsensitiveContains(searchText) ||
            $0.id.localizedCaseInsensitiveContains(searchText)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            if !isAvailable {
                setupRequiredNotice
            } else if isLoadingCatalog {
                loadingNotice
            } else {
                HSplitView {
                    sidebar
                        .frame(minWidth: 190, idealWidth: 210, maxWidth: 240)
                    detail
                        .frame(minWidth: 400, maxWidth: .infinity, maxHeight: .infinity)
                }
                if runner.isRunning || runner.done {
                    Divider()
                    progressPanel
                }
                Divider()
                footer
            }
        }
        .frame(width: 760, height: 600)
        .background(.ultraThinMaterial)
        .tint(.brand)
        .onAppear { loadCatalog(); loadInstalled() }
        .onChange(of: backend.activePrefix) { _ in loadInstalled() }
    }

    // MARK: - Header / footer

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "shippingbox.fill")
                .font(.system(size: 20))
                .foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(L("Winetricks App Store"))
                    .font(.title2)
                    .fontWeight(.bold)
                Text(L("Browse and install components into this bottle via winetricks."))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button { dismiss() } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title2)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(20)
    }

    private var footer: some View {
        HStack(spacing: 4) {
            Text(L("Powered by"))
                .font(.caption2)
                .foregroundStyle(.secondary)
            Link("winetricks", destination: URL(string: "https://github.com/Winetricks/winetricks")!)
                .font(.caption2)
            Spacer()
            if !allVerbs.isEmpty {
                Text(String(format: L("%d components"), allVerbs.count))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 8)
    }

    private var loadingNotice: some View {
        VStack(spacing: 12) {
            Spacer()
            ProgressView().controlSize(.regular)
            Text(L("Loading catalog…"))
                .font(.callout)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var setupRequiredNotice: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "wrench.and.screwdriver")
                .font(.system(size: 36))
                .foregroundStyle(.secondary)
            Text(L("Run Setup first"))
                .font(.headline)
            Text(L("This bottle needs the app's one-time Setup step completed before you can install components here."))
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 360)
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(20)
    }

    // MARK: - Sidebar

    private var sidebar: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 2) {
                sidebarRow(id: "all", label: L("All"), systemImage: "square.grid.2x2",
                          count: allVerbs.count)

                Divider().padding(.vertical, 6)

                ForEach(WinetricksCatalog.categoryOrder, id: \.self) { categoryId in
                    let info = WinetricksCatalog.info(for: categoryId)
                    sidebarRow(id: categoryId, label: info.name, systemImage: info.systemImage,
                              count: allVerbs.filter { $0.category == categoryId }.count)
                }
            }
            .padding(8)
        }
        .background(.quaternary.opacity(0.15))
    }

    private func sidebarRow(id: String, label: String, systemImage: String, count: Int) -> some View {
        Button {
            selectedCategory = id
        } label: {
            HStack {
                Label(label, systemImage: systemImage)
                Spacer()
                Text("\(count)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.vertical, 6)
            .padding(.horizontal, 8)
            .background(
                selectedCategory == id ? Color.accentColor.opacity(0.18) : Color.clear,
                in: RoundedRectangle(cornerRadius: 6)
            )
        }
        .buttonStyle(.plain)
    }

    // MARK: - Detail

    private var detail: some View {
        VStack(spacing: 0) {
            searchField
                .padding(12)
            Divider()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(filteredVerbs) { verb in
                        WinetricksVerbRow(
                            verb: verb,
                            isInstalled: installedVerbs.contains(verb.id),
                            isBusy: runner.isRunning && runner.currentVerb == verb.id,
                            disabled: runner.isRunning,
                            onInstall: { install(verb.id) },
                            onRepair: { install(verb.id, force: true) }
                        )
                        Divider()
                    }
                    if filteredVerbs.isEmpty {
                        Text(L("No matches."))
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .padding(.top, 12)
                            .padding(.horizontal, 16)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
            }
        }
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
            TextField(L("Search by name or winetricks id…"), text: $searchText)
                .textFieldStyle(.plain)
            if !searchText.isEmpty {
                Button { searchText = "" } label: {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(8)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Progress panel

    private var progressPanel: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                if runner.isRunning {
                    ProgressView().controlSize(.small)
                } else if runner.failed {
                    Image(systemName: "xmark.circle.fill").foregroundStyle(.red)
                } else {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                }
                Text(runner.isRunning
                     ? (runner.currentVerb.isEmpty ? L("Starting…") : String(format: L("Installing %@…"), runner.currentVerb))
                     : (runner.failed ? L("Finished with errors") : L("Done!")))
                    .font(.caption)
                    .foregroundColor(runner.isRunning ? .secondary : (runner.failed ? .red : .green))
                Spacer()
                if runner.isRunning {
                    Button(L("Cancel")) { Task { await runner.cancel(backend: backend) } }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                } else {
                    Button(L("Dismiss")) { runner.reset() }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
            }

            ScrollViewReader { proxy in
                ScrollView {
                    Text(runner.logLines.joined(separator: "\n"))
                        .font(.system(.caption2, design: .monospaced))
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                        .id("logBottom")
                }
                .frame(height: 100)
                .background(.black.opacity(0.25))
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .onChange(of: runner.logLines) { _ in
                    proxy.scrollTo("logBottom", anchor: .bottom)
                }
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }

    // MARK: - Actions

    private func install(_ verb: String, force: Bool = false) {
        guard let prefix = backend.activePrefix, !runner.isRunning else { return }
        Task {
            await runner.run(verbs: [verb], force: force, prefix: prefix, backend: backend)
            await refreshInstalled()
        }
    }

    private func loadCatalog() {
        guard allVerbs.isEmpty else { return }
        isLoadingCatalog = true
        Task {
            allVerbs = await backend.getWinetricksCatalog()
            isLoadingCatalog = false
        }
    }

    private func loadInstalled() {
        Task { await refreshInstalled() }
    }

    private func refreshInstalled() async {
        guard let prefix = backend.activePrefix else {
            installedVerbs = []
            return
        }
        installedVerbs = await backend.winetricksListInstalled(prefix: prefix)
    }
}

private struct WinetricksVerbRow: View {
    let verb: WinetricksVerb
    let isInstalled: Bool
    let isBusy: Bool
    let disabled: Bool
    let onInstall: () -> Void
    let onRepair: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(verb.title)
                    .font(.body)
                    .lineLimit(2)
                HStack(spacing: 6) {
                    Text(verb.id)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(.quaternary.opacity(0.6), in: RoundedRectangle(cornerRadius: 4))
                    if !verb.publisher.isEmpty {
                        Text(verb.year.isEmpty ? verb.publisher : "\(verb.publisher) · \(verb.year)")
                            .font(.caption2)
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            Spacer()
            if isBusy {
                ProgressView().controlSize(.small)
            } else if isInstalled {
                HStack(spacing: 6) {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .help(L("Installed"))
                    Button(L("Reinstall")) { onRepair() }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .disabled(disabled)
                }
            } else {
                Button(L("Install")) { onInstall() }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(disabled)
            }
        }
        .padding(.vertical, 8)
    }
}
