import SwiftUI

struct ContentView: View {
    @EnvironmentObject var backend: BackendClient
    @EnvironmentObject var announcements: AnnouncementChecker
    @State private var searchText = ""
    @State private var showCreateBottle = false
    @State private var showSettings = false
    @State private var showAnnouncement = false
    @State private var newBottleName = ""

    var filteredGames: [Game] {
        if searchText.isEmpty { return backend.games }
        return backend.games.filter { $0.name.localizedCaseInsensitiveContains(searchText) }
    }

    private var activeBottle: Bottle? {
        guard let prefix = backend.activePrefix else { return nil }
        return backend.bottles.first { $0.path == prefix }
    }

    var body: some View {
        NavigationSplitView {
            SidebarView(showCreateBottle: $showCreateBottle)
        } detail: {
            ZStack {
                
                Color.clear

                if backend.activePrefix == nil {
                    NoPrefixView()
                } else if backend.games.isEmpty {
                    if activeBottle?.isSteamBottle ?? true {
                        SteamLandingView()
                    } else {
                        EmptyBottleLandingView()
                    }
                } else {
                    GameGridView(games: filteredGames, searchText: $searchText)
                }
            }
            .background(.ultraThinMaterial)
        }
        .navigationSplitViewStyle(.balanced)
        .sheet(isPresented: $showCreateBottle) {
            CreateBottleSheet()
        }
        .sheet(isPresented: $showSettings) {
            SettingsSheet()
        }
        .sheet(isPresented: $showAnnouncement) {
            AnnouncementSheet(checker: announcements)
        }
        .onChange(of: announcements.hasNewAnnouncement) { _, hasNew in
            if hasNew { showAnnouncement = true }
        }
        .toolbar {
            ToolbarItem(placement: .automatic) {
                Button {
                    showSettings = true
                } label: {
                    Image(systemName: "gear")
                }
            }
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
                .foregroundStyle(.cyan.opacity(0.8))
                .padding(.bottom, 8)

            Text(customExeName?.uppercased() ?? "STEAM")
                .font(.system(size: 48, weight: .bold, design: .default))
                .tracking(4)
                .foregroundStyle(.primary)

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
            .tint(backend.steamRunning ? .red : .cyan)
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
    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "plus.circle")
                .font(.system(size: 56))
                .foregroundStyle(.secondary)
            Text("No bottle selected")
                .font(.title)
                .fontWeight(.bold)
            Text("Create a bottle to get started.")
                .foregroundStyle(.secondary)
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
                .foregroundStyle(.cyan.opacity(0.8))
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
                .tint(backend.steamRunning ? .red : .cyan)
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
                .buttonStyle(.borderedProminent)
                .tint(.cyan)
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
            Text("The compatibility list is updated on the Discord server. Launch the launcher anyway?")
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
                // Auto-dismiss after 6s. Re-fires if a new error replaces the current one.
                try? await Task.sleep(nanoseconds: 6_000_000_000)
                if !Task.isCancelled && backend.lastError == error {
                    backend.lastError = nil
                }
            }
        }
    }
}
