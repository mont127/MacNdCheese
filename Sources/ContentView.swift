import SwiftUI

struct ContentView: View {
    @EnvironmentObject var backend: BackendClient
    @State private var searchText = ""
    @State private var showCreateBottle = false
    @State private var showSettings = false
    @State private var newBottleName = ""

    var filteredGames: [Game] {
        if searchText.isEmpty { return backend.games }
        return backend.games.filter { $0.name.localizedCaseInsensitiveContains(searchText) }
    }

    var body: some View {
        NavigationSplitView {
            SidebarView(showCreateBottle: $showCreateBottle)
        } detail: {
            ZStack {
                // Transparent base so the window vibrancy shows
                Color.clear

                if backend.games.isEmpty && backend.activePrefix != nil {
                    SteamLandingView()
                } else if backend.activePrefix == nil {
                    NoPrefixView()
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

// MARK: - Steam Landing

struct SteamLandingView: View {
    @EnvironmentObject var backend: BackendClient
    @State private var isLaunching = false

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            // Steam icon
            Image(systemName: "gamecontroller.fill")
                .font(.system(size: 80))
                .foregroundStyle(.cyan.opacity(0.8))
                .padding(.bottom, 8)

            Text("STEAM")
                .font(.system(size: 48, weight: .bold, design: .default))
                .tracking(4)
                .foregroundStyle(.white)

            Spacer().frame(height: 32)

            // Big launch button
            Button {
                guard let prefix = backend.activePrefix else { return }
                isLaunching = true
                Task {
                    await backend.launchSteam(prefix: prefix)
                    isLaunching = false
                }
            } label: {
                HStack(spacing: 8) {
                    if isLaunching {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Image(systemName: "play.fill")
                    }
                    Text(backend.steamRunning ? "Steam Running" : "Launch")
                        .fontWeight(.bold)
                }
                .frame(width: 160, height: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(.cyan)
            .controlSize(.large)
            .disabled(backend.activePrefix == nil || isLaunching)

            Spacer().frame(height: 32)

            // Secondary actions
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
