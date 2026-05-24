import SwiftUI

struct EpicLibraryView: View {
    @EnvironmentObject var backend: BackendClient
    let games: [Game]
    @Binding var searchText: String
    var isFetching: Bool = false

    private let columns = [
        GridItem(.adaptive(minimum: 160, maximum: 200), spacing: 16)
    ]

    private var displayedGames: [Game] {
        searchText.isEmpty
            ? games
            : games.filter { $0.name.localizedCaseInsensitiveContains(searchText) }
    }

    var body: some View {
        ScrollView {
            if isFetching && displayedGames.isEmpty {
                VStack(spacing: 16) {
                    Spacer().frame(height: 60)
                    ProgressView()
                        .controlSize(.large)
                    Text("Fetching your library…")
                        .foregroundStyle(.secondary)
                        .font(.subheadline)
                    Spacer()
                }
                .frame(maxWidth: .infinity)
            } else if displayedGames.isEmpty {
                VStack(spacing: 12) {
                    Spacer().frame(height: 60)
                    Image(systemName: "tray")
                        .font(.system(size: 48))
                        .foregroundStyle(.secondary)
                    Text("No games found")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                    Spacer()
                }
                .frame(maxWidth: .infinity)
            } else {
                LazyVGrid(columns: columns, spacing: 16) {
                    ForEach(displayedGames) { game in
                        EpicGameCard(game: game)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
        }
        .contentMargins(.top, 20, for: .scrollContent)
        .scrollClipDisabled()
    }
}

// MARK: - Epic game card

struct EpicGameCard: View {
    @EnvironmentObject var backend: BackendClient
    let game: Game

    @State private var isHovering = false
    @State private var showLaunchOptions = false
    @State private var coverImage: NSImage? = nil
    @State private var isLaunching = false

    // Install tracking
    @State private var installing = false
    @State private var installProgress: Double = 0
    @State private var installPollTask: Task<Void, Never>? = nil

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .topTrailing) {
                coverArea
                if isHovering && game.isInstalled && !installing {
                    gearButton
                }
            }
            .frame(height: 220)

            HStack(spacing: 0) {
                Text(game.name)
                    .font(.subheadline)
                    .fontWeight(.medium)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, 8)
                if installing {
                    Button { cancelInstall() } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 16))
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                    .padding(.trailing, 8)
                }
            }
            .padding(.vertical, 8)
        }
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(
                    isHovering ? Color.accentColor.opacity(0.5) : Color.primary.opacity(0.08),
                    lineWidth: 1
                )
        )
        .scaleEffect(isHovering ? 1.02 : 1.0)
        .shadow(color: isHovering ? Color.accentColor.opacity(0.2) : .clear, radius: 12)
        .animation(.easeOut(duration: 0.2), value: isHovering)
        .onHover { hovering in isHovering = hovering }
        .onAppear { loadCover() }
        .contextMenu { contextMenuItems }
        .sheet(isPresented: $showLaunchOptions) {
            GameLaunchSheet(game: game, coverImage: coverImage)
        }
    }

    @ViewBuilder
    private var coverArea: some View {
        Button {
            if game.isInstalled { launch() }
            else if !installing { startInstall() }
        } label: {
            ZStack {
                // Background placeholder
                RoundedRectangle(cornerRadius: 12)
                    .fill(.ultraThinMaterial)

                // Cover image — always full opacity
                if let image = coverImage {
                    Image(nsImage: image)
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                } else {
                    Image(systemName: "gamecontroller.fill")
                        .font(.system(size: 32))
                        .foregroundStyle(.secondary)
                }

                // Gray overlay: full when not installed, left-to-right reveal while downloading
                if installing {
                    Rectangle()
                        .fill(.black.opacity(0.55))
                        .scaleEffect(
                            x: CGFloat(1.0 - installProgress / 100.0),
                            y: 1.0,
                            anchor: .trailing
                        )
                        .animation(.linear(duration: 2.5), value: installProgress)
                } else if !game.isInstalled {
                    Rectangle()
                        .fill(.black.opacity(0.55))
                }

                // Hover overlay for installed games
                if isHovering && game.isInstalled {
                    Rectangle()
                        .fill(Color.primary.opacity(0.3))
                }

                // Center indicator: download arrow or progress %
                if !game.isInstalled || installing {
                    VStack(spacing: 6) {
                        if installing {
                            Text("\(Int(installProgress))%")
                                .font(.system(.title2, design: .monospaced).weight(.bold))
                                .foregroundStyle(.white)
                                .shadow(radius: 4)
                                .contentTransition(.numericText())
                                .animation(.default, value: installProgress)
                        } else {
                            Image(systemName: "arrow.down.circle.fill")
                                .font(.system(size: 36))
                                .foregroundStyle(.white.opacity(0.9))
                                .shadow(radius: 4)
                        }
                        if isHovering {
                            Text(installing ? "Installing…" : "Download")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.white.opacity(0.9))
                        }
                    }
                }

                // Launching spinner
                if isLaunching {
                    ProgressView().controlSize(.large).tint(.white)
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
    }

    @ViewBuilder
    private var gearButton: some View {
        Button { showLaunchOptions = true } label: {
            Image(systemName: "gearshape.fill")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(.white)
                .padding(7)
                .background(.ultraThinMaterial, in: Circle())
        }
        .buttonStyle(.plain)
        .padding(8)
        .transition(.opacity.combined(with: .scale(scale: 0.8)))
        .accessibilityLabel("Launch options for \(game.name)")
    }

    @ViewBuilder
    private var contextMenuItems: some View {
        if game.isInstalled {
            Button("Launch") { launch() }
            Button("Launch Options…") { showLaunchOptions = true }
            if let exe = game.exe {
                Button("Show in Finder") {
                    NSWorkspace.shared.selectFile(exe, inFileViewerRootedAtPath: "")
                }
            }
        } else if !installing {
            Button("Download & Install") { startInstall() }
        } else {
            Button("Cancel Download") { cancelInstall() }
        }
    }

    private func launch() {
        guard let prefix = backend.activePrefix,
              let appName = game.epicAppName,
              !isLaunching else { return }
        isLaunching = true
        Task {
            let cfg = await backend.getGameConfig(prefix: prefix, appid: game.appid)
            let esync = cfg["esync"] as? Bool ?? true
            let msync = cfg["msync"] as? Bool ?? true
            await backend.epicLaunchGame(
                prefix: prefix,
                appName: appName,
                backend: cfg["backend"] as? String ?? "auto",
                retinaMode: cfg["retina_mode"] as? Bool ?? (NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false),
                metalHud: cfg["metal_hud"] as? Bool ?? false,
                esync: msync ? false : esync,
                msync: msync,
                customEnv: cfg["custom_env"] as? String ?? ""
            )
            isLaunching = false
        }
    }

    private func startInstall() {
        guard let prefix = backend.activePrefix, let appName = game.epicAppName else { return }
        installing = true
        installProgress = 0
        Task {
            guard await backend.epicInstallGame(prefix: prefix, appName: appName) != nil else {
                installing = false
                return
            }
            startProgressPolling(appName: appName, prefix: prefix)
        }
    }

    private func startProgressPolling(appName: String, prefix: String) {
        installPollTask?.cancel()
        installPollTask = Task {
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard !Task.isCancelled else { break }
                if let prog = await backend.epicInstallProgress(appName: appName) {
                    installProgress = prog.progress
                    if prog.done {
                        installing = false
                        await backend.scanGames(prefix: prefix)
                        break
                    }
                } else {
                    break
                }
            }
            installing = false
        }
    }

    private func cancelInstall() {
        installPollTask?.cancel()
        installing = false
        guard let appName = game.epicAppName else { return }
        Task { await backend.epicCancelInstall(appName: appName) }
    }

    private func loadCover() {
        guard let urlString = game.coverUrl, !urlString.isEmpty,
              let url = URL(string: urlString) else { return }
        Task.detached(priority: .background) {
            do {
                let (data, _) = try await URLSession.shared.data(from: url)
                if let image = NSImage(data: data) {
                    await MainActor.run { coverImage = image }
                }
            } catch {}
        }
    }
}
