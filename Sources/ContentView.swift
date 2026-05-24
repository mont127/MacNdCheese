import SwiftUI

struct ContentView: View {
    @EnvironmentObject var backend: BackendClient
    @EnvironmentObject var announcements: AnnouncementChecker
    @Environment(\.openSettings) private var openSettings
    @State private var searchText = ""
    @State private var showCreateBottle = false
    @State private var showAnnouncement = false
    @State private var showStore = false
    @State private var showEpicStore = false
    @State private var newBottleName = ""
    @State private var showKillConfirmation = false

    @ViewBuilder private var killWineserverButton: some View {
        Button {
            showKillConfirmation = true
        } label: {
            Image(systemName: "stop.circle").foregroundStyle(.red)
        }
        .help("Kill Wineserver")
        .disabled(backend.activePrefix == nil)
    }

    @ViewBuilder private var settingsButtons: some View {
                Button { openSettings() } label: { Image(systemName: "gear") }
            .help("Settings")
            .accessibilityLabel("Settings")
        if announcements.hasNewAnnouncement {
            Button { showAnnouncement = true } label: {
                Image(systemName: "bell.badge.fill").symbolRenderingMode(.multicolor)
            }
            .help("New Announcement")
            .accessibilityLabel("New Announcement")
        }
        if activeBottle?.isEpicBottle == true && backend.epicAuthenticated {
            Button { showEpicStore = true } label: {
                Label("Open Store", systemImage: "cart")
            }
            .help("Open Epic Games Store")
        }
    }

    var filteredGames: [Game] {
        if searchText.isEmpty { return backend.games }
        return backend.games.filter { $0.name.localizedCaseInsensitiveContains(searchText) }
    }

    private var activeBottle: Bottle? {
        guard let prefix = backend.activePrefix else { return nil }
        return backend.bottles.first { $0.path == prefix }
    }

    private var detailTitle: String {
        if showStore { return "Store" }
        if let bottle = activeBottle { return bottle.name }
        return "MacNCheese"
    }

    private var detailSubtitle: String {
        if showStore || backend.activePrefix == nil { return "" }
        return "Library"
    }

    var body: some View {
        NavigationSplitView {
            SidebarView(showCreateBottle: $showCreateBottle, showStore: $showStore)
        } detail: {
            ZStack {
                Color.clear

                if showStore {
                    StoreView()
                        .transition(.opacity)
                } else if backend.activePrefix == nil {
                    NoPrefixView(showCreateBottle: $showCreateBottle)
                        .transition(.opacity)
                } else if activeBottle?.isEpicBottle == true {
                    EpicLandingView(searchText: $searchText)
                        .id(backend.activePrefix)
                        .transition(.opacity)
                } else if backend.games.isEmpty {
                    if activeBottle?.isSteamBottle ?? true {
                        SteamLandingView()
                            .transition(.opacity)
                    } else {
                        EmptyBottleLandingView()
                            .transition(.opacity)
                    }
                } else {
                    GameGridView(games: filteredGames, searchText: $searchText)
                        .transition(.opacity)
                }
            }
            .animation(.easeInOut(duration: 0.22), value: backend.activePrefix)
            .animation(.easeInOut(duration: 0.22), value: showStore)
            .animation(.easeInOut(duration: 0.22), value: backend.games.isEmpty)
            .background(Color(.windowBackgroundColor))
            .navigationTitle(detailTitle)
            .navigationSubtitle(detailSubtitle)
        }
        .onChange(of: backend.activePrefix) { _, _ in showStore = false }
        .navigationSplitViewStyle(.balanced)
        .searchable(text: $searchText, placement: .toolbar, prompt: "Search games")
        .onReceive(NotificationCenter.default.publisher(for: .createNewBottle)) { _ in
            showCreateBottle = true
        }
        .sheet(isPresented: $showCreateBottle) {
            CreateBottleSheet()
        }
        .sheet(isPresented: $showEpicStore) {
            EpicStoreSheet(isPresented: $showEpicStore)
        }
        .sheet(isPresented: $showAnnouncement) {
            AnnouncementSheet(checker: announcements)
        }
        .onChange(of: showStore) { _, isStore in
            // Clear the sidebar selection while in store mode so List
            // doesn't draw a highlighted bottle row.
            if isStore { /* no-op */ }
        }
        .toolbar {
            ToolbarItem(placement: .destructiveAction) {
                killWineserverButton
            }
            ToolbarItemGroup(placement: .primaryAction) {
                settingsButtons
            }
        }
        .alert("Kill Wineserver?", isPresented: $showKillConfirmation) {
            Button("Kill", role: .destructive) {
                guard let prefix = backend.activePrefix else { return }
                Task { await backend.killWineserver(prefix: prefix) }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This will forcefully terminate all Wine processes in the current bottle. Any unsaved game progress will be lost.")
        }
    }
}



struct SteamLandingView: View {
    @EnvironmentObject var backend: BackendClient
    @State private var isLaunching = false

    private var activeBottle: Bottle? {
        guard let prefix = backend.activePrefix else { return nil }
        return backend.bottles.first { $0.path == prefix }
    }

    private var customExeName: String? {
        guard let exe = activeBottle?.launcherExe, !exe.isEmpty else { return nil }
        return URL(fileURLWithPath: exe).deletingPathExtension().lastPathComponent
    }

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            Image(systemName: "gamecontroller.fill")
                .font(.system(size: 80))
                .foregroundStyle(Color.accentColor.opacity(0.8))
                .padding(.bottom, 8)

            Text(customExeName?.uppercased() ?? "STEAM")
                .font(.system(.largeTitle, design: .default).weight(.bold))
                .tracking(4)
                .foregroundStyle(.primary)

            Text(customExeName != nil ? "Launch to browse your games." : "Launch Steam to browse and install games.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .padding(.top, 4)

            Spacer().frame(height: 32)

            Button {
                guard let prefix = backend.activePrefix else { return }
                if backend.steamRunning {
                    Task {
                        await backend.killWineserver(prefix: prefix)
                        backend.steamRunning = false
                    }
                } else {
                    isLaunching = true
                    Task {
                        await backend.launchSteam(prefix: prefix)
                        isLaunching = false
                    }
                }
            } label: {
                HStack(spacing: 8) {
                    if isLaunching {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Image(systemName: backend.steamRunning ? "stop.fill" : "play.fill")
                    }
                    Text(backend.steamRunning ? "Close \(customExeName ?? "Steam")" : "Launch")
                        .fontWeight(.bold)
                }
                .frame(width: 160, height: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(backend.steamRunning ? .red : Color.accentColor)
            .controlSize(.large)
            .disabled(backend.activePrefix == nil || isLaunching)

            Spacer().frame(height: 32)

            HStack(spacing: 12) {
                Button("Run Installer") {
                    let panel = NSOpenPanel()
                    panel.allowedContentTypes = [.exe]
                    panel.canChooseFiles = true
                    if panel.runModal() == .OK, let url = panel.url,
                       let prefix = backend.activePrefix {
                        Task {
                            await backend.launchGame(prefix: prefix, exe: url.path)
                        }
                    }
                }
                .buttonStyle(.bordered)

                Button("Add Game") {
                    addManualGame()
                }
                .buttonStyle(.bordered)
            }

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func addManualGame() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.exe]
        panel.canChooseFiles = true
        panel.title = "Select Game EXE"
        if panel.runModal() == .OK, let url = panel.url,
           let prefix = backend.activePrefix {
            let name = url.deletingPathExtension().lastPathComponent
            Task {
                await backend.addManualGame(prefix: prefix, name: name, exe: url.path)
            }
        }
    }
}

struct NoPrefixView: View {
    @Binding var showCreateBottle: Bool

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "plus.circle")
                .font(.system(size: 56))
                .foregroundStyle(Color.accentColor.opacity(0.8))
            Text("No bottle selected")
                .font(.title)
                .fontWeight(.bold)
            Text("Create a bottle to get started.")
                .foregroundStyle(.secondary)
            Button("Create Bottle") {
                showCreateBottle = true
            }
            .buttonStyle(.borderedProminent)
            .padding(.top, 8)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct EmptyBottleLandingView: View {
    @EnvironmentObject var backend: BackendClient
    @State private var isLaunching = false
    @State private var showCompatibilityListNotice = false

    private var activeBottle: Bottle? {
        guard let prefix = backend.activePrefix else { return nil }
        return backend.bottles.first { $0.path == prefix }
    }

    private var launcherExe: String? {
        guard let exe = activeBottle?.launcherExe, !exe.isEmpty else { return nil }
        return exe
    }

    private var launcherName: String {
        launcherExe.map { URL(fileURLWithPath: $0).deletingPathExtension().lastPathComponent } ?? "Launcher"
    }

    var body: some View {
        VStack(spacing: 0) {
            Spacer()
            Image(systemName: "wineglass")
                .font(.system(size: 72))
                .foregroundStyle(Color.accentColor.opacity(0.8))
                .padding(.bottom, 12)
            Text("No Games")
                .font(.title)
                .fontWeight(.bold)
            Text("Add a game or run an installer to get started.")
                .foregroundStyle(.secondary)
                .padding(.top, 4)
            Spacer().frame(height: 28)
            if launcherExe != nil {
                Button {
                    guard let prefix = backend.activePrefix else { return }
                    if backend.steamRunning {
                        Task {
                            await backend.killWineserver(prefix: prefix)
                            backend.steamRunning = false
                        }
                    } else {
                        showCompatibilityListNotice = true
                    }
                } label: {
                    HStack(spacing: 8) {
                        if isLaunching {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: backend.steamRunning ? "stop.fill" : "play.fill")
                        }
                        Text(backend.steamRunning ? "Close \(launcherName)" : "Launch \(launcherName)")
                            .fontWeight(.bold)
                    }
                    .frame(minWidth: 160)
                }
                .buttonStyle(.borderedProminent)
                .tint(backend.steamRunning ? .red : Color.accentColor)
                .controlSize(.large)
                .disabled(isLaunching)
                Spacer().frame(height: 20)
            }
            HStack(spacing: 12) {
                Button("Run Installer") {
                    let panel = NSOpenPanel()
                    panel.allowedContentTypes = [.exe]
                    panel.canChooseFiles = true
                    if panel.runModal() == .OK, let url = panel.url,
                       let prefix = backend.activePrefix {
                        Task { await backend.launchGame(prefix: prefix, exe: url.path) }
                    }
                }
                .buttonStyle(.bordered)
                .controlSize(.large)

                Button("Add Game") {
                    addManualGame()
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .alert("Compatibility List Update", isPresented: $showCompatibilityListNotice) {
            Button("Launch") {
                guard let prefix = backend.activePrefix else { return }
                launchLauncher(prefix: prefix)
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The compatibility list may have changed. Launch the launcher anyway?")
        }
    }

    private func launchLauncher(prefix: String) {
        isLaunching = true
        Task {
            await backend.launchLauncher(prefix: prefix)
            isLaunching = false
        }
    }

    private func addManualGame() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.exe]
        panel.canChooseFiles = true
        panel.title = "Select Game EXE"
        if panel.runModal() == .OK, let url = panel.url,
           let prefix = backend.activePrefix {
            let name = url.deletingPathExtension().lastPathComponent
            Task { await backend.addManualGame(prefix: prefix, name: name, exe: url.path) }
        }
    }
}


struct ErrorBannerView: View {
    @EnvironmentObject var backend: BackendClient

    var body: some View {
        if let error = backend.lastError {
            HStack(spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.red)
                Text(error)
                    .font(.callout)
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Button {
                    backend.lastError = nil
                } label: {
                    Image(systemName: "xmark")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)
                        .padding(4)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Dismiss")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(Color.red.opacity(0.5), lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.15), radius: 8, y: 2)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .transition(.move(edge: .top).combined(with: .opacity))
            .task(id: error) {
                
                try? await Task.sleep(nanoseconds: 6_000_000_000)
                if !Task.isCancelled && backend.lastError == error {
                    backend.lastError = nil
                }
            }
        }
    }
}
