import SwiftUI
import UniformTypeIdentifiers


struct EpicLibraryView: View {
    @EnvironmentObject var backend: BackendClient
    let games: [Game]
    @Binding var searchText: String
    var isFetching: Bool = false

    @State private var gameOrder: [String] = []
    @State private var draggingAppid: String? = nil
    @State private var dropTargetAppid: String? = nil

    private let columns = [
        GridItem(.adaptive(minimum: 160, maximum: 200), spacing: 16)
    ]

    private var orderedGames: [Game] {
        if gameOrder.isEmpty { return games }
        let orderMap = Dictionary(uniqueKeysWithValues: gameOrder.enumerated().map { ($1, $0) })
        return games.sorted {
            let ia = orderMap[$0.appid] ?? Int.max
            let ib = orderMap[$1.appid] ?? Int.max
            return ia == ib ? $0.name.lowercased() < $1.name.lowercased() : ia < ib
        }
    }

    private var displayedGames: [Game] {
        searchText.isEmpty
            ? orderedGames
            : orderedGames.filter { $0.name.localizedCaseInsensitiveContains(searchText) }
    }

    var body: some View {
        ScrollView {
            if isFetching && displayedGames.isEmpty {
                VStack(spacing: 16) {
                    Spacer().frame(height: 60)
                    ProgressView().controlSize(.large)
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
                            .opacity(draggingAppid == game.appid ? 0.45 : 1.0)
                            .overlay(
                                RoundedRectangle(cornerRadius: 14)
                                    .stroke(
                                        dropTargetAppid == game.appid ? Color.accentColor : Color.clear,
                                        lineWidth: 2
                                    )
                            )
                            .onDrag {
                                draggingAppid = game.appid
                                return NSItemProvider(object: game.appid as NSString)
                            }
                            .onDrop(
                                of: [UTType.plainText],
                                isTargeted: Binding(
                                    get: { dropTargetAppid == game.appid },
                                    set: { targeted in dropTargetAppid = targeted ? game.appid : nil }
                                )
                            ) { (_: [NSItemProvider]) -> Bool in
                                guard let from = draggingAppid, from != game.appid else {
                                    draggingAppid = nil; return false
                                }
                                moveGame(from: from, after: game.appid)
                                draggingAppid = nil
                                return true
                            }
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
        }
        .contentMargins(.top, 20, for: .scrollContent)
        .scrollClipDisabled()
        .onAppear { loadGameOrder() }
        .onChange(of: backend.activePrefix) { loadGameOrder() }
        .onChange(of: games) {
            let known = Set(gameOrder)
            let newIds = games.map { $0.appid }.filter { !known.contains($0) }
            gameOrder = gameOrder.filter { id in games.contains { $0.appid == id } } + newIds
        }
    }

    private func moveGame(from sourceAppid: String, after targetAppid: String) {
        var order = orderedGames.map { $0.appid }
        guard let fromIdx = order.firstIndex(of: sourceAppid),
              let toIdx = order.firstIndex(of: targetAppid) else { return }
        order.swapAt(fromIdx, toIdx)
        gameOrder = order
        guard let prefix = backend.activePrefix else { return }
        Task { await backend.setGameOrder(prefix: prefix, order: order) }
    }

    private func loadGameOrder() {
        guard let prefix = backend.activePrefix else {
            gameOrder = games.map { $0.appid }
            return
        }
        Task {
            let saved = await backend.getGameOrder(prefix: prefix)
            if saved.isEmpty {
                gameOrder = games.map { $0.appid }
            } else {
                let known = Set(saved)
                let newIds = games.map { $0.appid }.filter { !known.contains($0) }
                gameOrder = saved.filter { id in games.contains { $0.appid == id } } + newIds
            }
        }
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

    private var downloadState: EpicDownloadState? {
        guard let appName = game.epicAppName,
              let state = backend.epicDownloads[appName],
              state.prefix == backend.activePrefix else { return nil }
        return state
    }

    private var installing: Bool { downloadState != nil && !(downloadState?.paused ?? false) }
    private var isPaused: Bool { downloadState?.paused ?? false }
    private var installProgress: Double { downloadState?.progress ?? 0 }
    private var isQueued: Bool { downloadState?.queued ?? false }
    private var queuePosition: Int { downloadState?.queuePosition ?? 0 }

    var body: some View {
        VStack(spacing: 0) {
            ZStack(alignment: .topTrailing) {
                coverArea
                if isHovering && game.isInstalled && !installing && !isPaused && !game.updateAvailable {
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
                // Pause button — only for the active (non-queued, non-paused) download
                if installing && !isQueued {
                    Button { pauseInstall() } label: {
                        Image(systemName: "pause.circle.fill")
                            .font(.system(size: 16))
                            .foregroundStyle(.secondary)
                    }
                    .buttonStyle(.plain)
                    .padding(.trailing, 4)
                }
                // Resume button — when paused
                if isPaused {
                    Button { resumeInstall() } label: {
                        Image(systemName: "play.circle.fill")
                            .font(.system(size: 16))
                            .foregroundStyle(.indigo)
                    }
                    .buttonStyle(.plain)
                    .padding(.trailing, 4)
                }
                // Cancel — active, queued, or paused
                if installing || isPaused {
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
            if isPaused { resumeInstall() }
            else if game.isInstalled && !game.updateAvailable { launch() }
            else if !installing { startInstall() }
        } label: {
            ZStack {
                RoundedRectangle(cornerRadius: 12)
                    .fill(.ultraThinMaterial)

                if let image = coverImage {
                    Image(nsImage: image)
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                } else {
                    Image(systemName: "gamecontroller.fill")
                        .font(.system(size: 32))
                        .foregroundStyle(.secondary)
                }

                // Progress wipe overlay for active downloads
                if installing && !isQueued {
                    Rectangle()
                        .fill(.black.opacity(0.55))
                        .scaleEffect(
                            x: CGFloat(1.0 - installProgress / 100.0),
                            y: 1.0,
                            anchor: .trailing
                        )
                        .animation(.linear(duration: 2.5), value: installProgress)
                } else if !game.isInstalled && !isPaused {
                    Rectangle()
                        .fill(.black.opacity(0.55))
                }

                if isLaunching {
                    LaunchingOverlay(cornerRadius: 12)
                } else if isHovering && game.isInstalled && !installing && !isPaused {
                    Rectangle()
                        .fill(Color.primary.opacity(0.3))
                }

                // Update badge
                if game.updateAvailable && game.isInstalled && !installing && !isPaused {
                    VStack {
                        HStack {
                            Text("UPDATE")
                                .font(.system(size: 9, weight: .bold))
                                .foregroundStyle(.white)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 3)
                                .background(.indigo, in: RoundedRectangle(cornerRadius: 4))
                                .padding(8)
                            Spacer()
                        }
                        Spacer()
                    }
                }

                // Center indicators
                if isLaunching {
                    VStack(spacing: 6) {
                        ProgressView().controlSize(.large).tint(.white)
                        if isHovering {
                            Text("Launching…")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.white.opacity(0.9))
                                .transition(.opacity)
                        }
                    }
                } else if game.updateAvailable && game.isInstalled && !installing && !isPaused && isHovering {
                    VStack(spacing: 6) {
                        Image(systemName: "arrow.down.circle.fill")
                            .font(.system(size: 36))
                            .foregroundStyle(.white.opacity(0.9))
                            .shadow(radius: 4)
                        Text("Update")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.white.opacity(0.9))
                    }
                } else if isPaused {
                    VStack(spacing: 6) {
                        Image(systemName: "pause.circle.fill")
                            .font(.system(size: 36))
                            .foregroundStyle(.white.opacity(0.9))
                            .shadow(radius: 4)
                        Text("\(Int(installProgress))% — Paused")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.white.opacity(0.9))
                    }
                } else if isQueued {
                    VStack(spacing: 6) {
                        Image(systemName: "clock.fill")
                            .font(.system(size: 28))
                            .foregroundStyle(.white.opacity(0.9))
                            .shadow(radius: 4)
                        Text("Queue #\(queuePosition)")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.white.opacity(0.9))
                    }
                } else if installing {
                    VStack(spacing: 6) {
                        Text("\(Int(installProgress))%")
                            .font(.system(.title2, design: .monospaced).weight(.bold))
                            .foregroundStyle(.white)
                            .shadow(radius: 4)
                            .contentTransition(.numericText())
                            .animation(.default, value: installProgress)
                        if isHovering {
                            Text("Installing…")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.white.opacity(0.9))
                        }
                    }
                } else if !game.isInstalled {
                    VStack(spacing: 6) {
                        Image(systemName: "arrow.down.circle.fill")
                            .font(.system(size: 36))
                            .foregroundStyle(.white.opacity(0.9))
                            .shadow(radius: 4)
                        if isHovering {
                            Text("Download")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(.white.opacity(0.9))
                        }
                    }
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
            if game.updateAvailable && !installing && !isPaused {
                Button("Update") { startInstall() }
            }
            Button("Launch Options…") { showLaunchOptions = true }
            if let exe = game.exe {
                Button("Show in Finder") {
                    NSWorkspace.shared.selectFile(exe, inFileViewerRootedAtPath: "")
                }
            }
        } else if isPaused {
            Button("Resume Download") { resumeInstall() }
            Button("Cancel Download") { cancelInstall() }
        } else if !installing {
            Button("Download & Install") { startInstall() }
        } else if isQueued {
            Button("Cancel Download") { cancelInstall() }
        } else {
            Button("Pause Download") { pauseInstall() }
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
        Task {
            _ = await backend.epicInstallGame(prefix: prefix, appName: appName)
            await backend.refreshEpicDownloads()
        }
    }

    private func pauseInstall() {
        guard let appName = game.epicAppName else { return }
        Task {
            await backend.epicPauseInstall(appName: appName)
            await backend.refreshEpicDownloads()
        }
    }

    private func resumeInstall() {
        guard let appName = game.epicAppName else { return }
        Task {
            await backend.epicResumeInstall(appName: appName)
            await backend.refreshEpicDownloads()
        }
    }

    private func cancelInstall() {
        guard let appName = game.epicAppName else { return }
        Task {
            await backend.epicCancelInstall(appName: appName)
            await backend.refreshEpicDownloads()
        }
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
