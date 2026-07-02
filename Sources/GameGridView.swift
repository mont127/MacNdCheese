import SwiftUI
import UniformTypeIdentifiers

struct GameGridView: View {
    @EnvironmentObject var backend: BackendClient
    let games: [Game]
    @Binding var searchText: String
    /// Open the in-pane game detail page (replaces the old modal launch sheet).
    var onOpenDetail: (Game) -> Void = { _ in }

    @State private var gameOrder: [String] = []
    @State private var draggingAppid: String? = nil
    @State private var dropTargetAppid: String? = nil
    @State private var isRefreshing = false

    private var activeBottle: Bottle? {
        guard let prefix = backend.activePrefix else { return nil }
        return backend.bottles.first { $0.path == prefix }
    }

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

    private var launcherName: String {
        guard let bottle = activeBottle else { return "Steam" }
        if let exe = bottle.launcherExe, !exe.isEmpty {
            return URL(fileURLWithPath: exe).deletingPathExtension().lastPathComponent
        }
        return bottle.isSteamBottle ? "Steam" : "Launcher"
    }

    @ViewBuilder
    private var scrollContent: some View {
        ScrollView {
            LazyVGrid(columns: columns, spacing: 16) {
                ForEach(displayedGames) { game in
                    GameCardView(
                        game: game,
                        onOpen: { onOpenDetail(game) },
                        onMoveToFront: { moveToFront(game.appid) },
                        onMoveToBack: { moveToBack(game.appid) }
                    )
                    .opacity(draggingAppid == game.appid ? 0.45 : 1.0)
                    .overlay(
                        RoundedRectangle(cornerRadius: 14)
                            .stroke(
                                dropTargetAppid == game.appid ? Color.brand : Color.clear,
                                lineWidth: 2
                            )
                    )
                    .onDrag {
                        let appid = game.appid
                        draggingAppid = appid
                        return NSItemProvider(object: appid as NSString)
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

            if !backend.apps.isEmpty {
                AppsSectionView(apps: backend.apps)
                    .padding(.bottom, 24)
            }
        }
        .contentMargins(.top, 20, for: .scrollContent)
        .scrollClipDisabled()
    }

    @ViewBuilder
    private var gameScrollView: some View {
        if #available(macOS 26, *) {
            scrollContent.scrollEdgeEffectStyle(.automatic, for: .top)
        } else {
            scrollContent
        }
    }

    var body: some View {
        gameScrollView
        .toolbar {
            ToolbarItem(placement: .navigation) {
                if let bottle = activeBottle,
                   bottle.isSteamBottle || !(bottle.launcherExe ?? "").isEmpty {
                    Button {
                        guard let prefix = backend.activePrefix else { return }
                        if backend.steamRunning {
                            Task {
                                await backend.killWineserver(prefix: prefix)
                                backend.steamRunning = false
                            }
                        } else {
                            Task {
                                if bottle.isSteamBottle {
                                    await backend.launchSteam(prefix: prefix)
                                } else {
                                    await backend.launchLauncher(prefix: prefix)
                                }
                            }
                        }
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: backend.steamRunning ? "stop.fill" : "play.fill")
                                .font(.caption)
                            Text(backend.steamRunning ? String(format: L("Close %@"), launcherName) : String(format: L("Open %@"), launcherName))
                        }
                    }
                    .buttonStyle(.bordered)
                    .tint(backend.steamRunning ? .red : Color.brand)
                }
            }
            ToolbarItem(placement: .primaryAction) {
                // Re-scan the bottle for games (new Steam installs, manually
                // copied games, etc.) without restarting the app.
                Button {
                    guard let prefix = backend.activePrefix, !isRefreshing else { return }
                    isRefreshing = true
                    Task {
                        await backend.scanGames(prefix: prefix)
                        isRefreshing = false
                    }
                } label: {
                    if isRefreshing {
                        ProgressView().controlSize(.small)
                    } else {
                        Label(L("Refresh"), systemImage: "arrow.clockwise")
                    }
                }
                .help(L("Re-scan the bottle for games."))
            }
            ToolbarItem(placement: .primaryAction) {
                // Non-Steam bottles: keep Add Game / Run Installer reachable even
                // after games exist (the empty-state buttons disappear once the
                // grid shows), so users can add multiple apps to the container.
                if let bottle = activeBottle, !bottle.isSteamBottle {
                    HStack(spacing: 8) {
                        Button { runInstaller() } label: {
                            Label(L("Run Installer"), systemImage: "shippingbox")
                        }
                        Button { addManualGame() } label: {
                            Label(L("Add Game"), systemImage: "plus")
                        }
                    }
                    .labelStyle(.titleAndIcon)
                }
            }
        }
        .onAppear { loadGameOrder() }
        .onChange(of: backend.activePrefix) { loadGameOrder() }
        .onChange(of: games) {
            let known = Set(gameOrder)
            let newIds = games.map { $0.appid }.filter { !known.contains($0) }
            gameOrder = gameOrder.filter { id in games.contains { $0.appid == id } } + newIds
        }
    }

    private func runInstaller() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.exe]
        panel.canChooseFiles = true
        if panel.runModal() == .OK, let url = panel.url, let prefix = backend.activePrefix {
            Task { await backend.launchGame(prefix: prefix, exe: url.path) }
        }
    }

    private func addManualGame() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.exe]
        panel.canChooseFiles = true
        panel.title = L("Select Game EXE")
        if panel.runModal() == .OK, let url = panel.url, let prefix = backend.activePrefix {
            let name = url.deletingPathExtension().lastPathComponent
            Task { await backend.addManualGame(prefix: prefix, name: name, exe: url.path) }
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

    private func moveToFront(_ appid: String) {
        var order = orderedGames.map { $0.appid }
        guard let idx = order.firstIndex(of: appid), idx > 0 else { return }
        order.remove(at: idx)
        order.insert(appid, at: 0)
        gameOrder = order
        guard let prefix = backend.activePrefix else { return }
        Task { await backend.setGameOrder(prefix: prefix, order: order) }
    }

    private func moveToBack(_ appid: String) {
        var order = orderedGames.map { $0.appid }
        guard let idx = order.firstIndex(of: appid), idx < order.count - 1 else { return }
        order.remove(at: idx)
        order.append(appid)
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

struct LaunchingOverlay: View {
    var cornerRadius: CGFloat = 12
    @State private var pulsing = false
    var body: some View {
        RoundedRectangle(cornerRadius: cornerRadius)
            .fill(Color.black.opacity(pulsing ? 0.55 : 0.2))
            .onAppear {
                withAnimation(.easeInOut(duration: 0.75).repeatForever(autoreverses: true)) {
                    pulsing = true
                }
            }
    }
}

struct GameCardView: View {
    @EnvironmentObject var backend: BackendClient
    let game: Game
    var onOpen: () -> Void = {}
    var onMoveToFront: (() -> Void)? = nil
    var onMoveToBack: (() -> Void)? = nil
    @State private var isHovering = false
    @State private var coverImage: NSImage?
    @State private var isLaunching = false
    // game.isReachable does a real FileManager syscall. Reading it directly
    // in `body` would re-run that on every re-render of this card — and
    // SwiftUI's ObservableObject invalidation is coarse: ANY @Published
    // change on backend (e.g. isLoadingLibrary flipping during a bottle
    // switch) re-renders every card in the grid, not just ones whose data
    // actually changed. For a library with many games that's a real
    // main-thread stall. Cache it instead and only recheck when it could
    // plausibly have changed: on first appearance, and when a drive
    // mounts/unmounts.
    @State private var isReachable = true

    var body: some View {
        VStack(spacing: 0) {
            // Cover image area
            ZStack(alignment: .topTrailing) {
                Button {
                    onOpen()
                } label: {
                    ZStack {
                        RoundedRectangle(cornerRadius: 12)
                            .fill(.ultraThinMaterial)
                            .frame(height: 220)

                        if let image = coverImage {
                            Image(nsImage: image)
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(height: 220)
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                        } else {
                            Image(systemName: "gamecontroller.fill")
                                .font(.system(size: 32))
                                .foregroundStyle(.secondary)
                        }

                        if isLaunching {
                            LaunchingOverlay(cornerRadius: 12)
                                .frame(height: 220)
                        } else if isHovering {
                            // No redundant play button: the whole tile (and the
                            // gear) opens the game page, where the real Launch +
                            // options live. Just a subtle hover scrim here.
                            RoundedRectangle(cornerRadius: 12)
                                .fill(Color.black.opacity(0.28))
                                .frame(height: 220)
                        }

                        if isLaunching {
                            VStack(spacing: 6) {
                                ProgressView()
                                    .controlSize(.large)
                                    .tint(.white)
                                if isHovering {
                                    Text(L("Launching…"))
                                        .font(.caption.weight(.semibold))
                                        .foregroundStyle(.white.opacity(0.9))
                                        .transition(.opacity)
                                }
                            }
                        }
                    }
                    .frame(height: 220)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(String(format: L("Launch %@"), game.name))

                if isHovering {
                    Button {
                        onOpen()
                    } label: {
                        Image(systemName: "gearshape.fill")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(.white)
                            .padding(7)
                            .background(.ultraThinMaterial, in: Circle())
                    }
                    .buttonStyle(.plain)
                    .padding(8)
                    .transition(.opacity.combined(with: .scale(scale: 0.8)))
                    .accessibilityLabel(String(format: L("Launch options for %@"), game.name))
                }
            }
            .frame(height: 220)
            .overlay(alignment: .topLeading) {
                if !isReachable {
                    Image(systemName: "externaldrive.badge.exclamationmark")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.white)
                        .padding(6)
                        .background(.orange, in: Circle())
                        .padding(8)
                        .help(L("This game's files aren't available — its drive isn't connected."))
                }
            }

            // Game name
            Text(game.name)
                .font(.subheadline)
                .fontWeight(.medium)
                .lineLimit(2)
                .multilineTextAlignment(.center)
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 8)
                .padding(.vertical, 8)
        }
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(
                    isHovering ? Color.brand.opacity(0.65) : Color.primary.opacity(0.08),
                    lineWidth: 1
                )
        )
        .opacity(isReachable ? 1.0 : 0.6)
        .scaleEffect(isHovering ? 1.02 : 1.0)
        .shadow(color: .black.opacity(0.28), radius: 7, y: 4)
        .shadow(color: isHovering ? Color.brand.opacity(0.35) : .clear, radius: 16)
        .animation(.easeOut(duration: 0.2), value: isHovering)
        .onHover { hovering in isHovering = hovering }
        .onAppear {
            loadCover()
            isReachable = game.isReachable
        }
        .onChange(of: backend.volumeChangeTick) { _, _ in
            isReachable = game.isReachable
        }
        .contextMenu {
            Button(L("Launch")) { directLaunch() }
            Button(L("Launch Options…")) { onOpen() }
            if let exe = game.exe {
                Button(L("Show in Finder")) {
                    NSWorkspace.shared.selectFile(exe, inFileViewerRootedAtPath: "")
                }
            }
            if onMoveToFront != nil || onMoveToBack != nil {
                Divider()
                if let move = onMoveToFront {
                    Button(L("Move to Front")) { move() }
                }
                if let move = onMoveToBack {
                    Button(L("Move to Back")) { move() }
                }
            }
            // Manually-added (non-Steam) games can be removed from the library
            // list. This only forgets the entry — the files on disk are untouched.
            if game.isManual {
                Divider()
                Button(L("Remove from Library"), role: .destructive) { removeFromLibrary() }
            }
        }
    }

    private func removeFromLibrary() {
        guard let prefix = backend.activePrefix, let exe = game.exe else { return }
        Task { await backend.removeManualGame(prefix: prefix, exe: exe) }
    }

    private func directLaunch() {
        guard let prefix = backend.activePrefix, !isLaunching, isReachable else { return }
        isLaunching = true
        Task {
            let cfg = await backend.getGameConfig(prefix: prefix, appid: game.appid)
            let exe = (cfg["exe"] as? String ?? "").isEmpty ? (game.exe ?? "") : (cfg["exe"] as! String)
            guard !exe.isEmpty else { isLaunching = false; return }
            let esync = cfg["esync"] as? Bool ?? true
            let msync = cfg["msync"] as? Bool ?? true
            let finalEsync = msync ? false : esync
            await backend.launchGame(
                prefix: prefix,
                exe: exe,
                args: cfg["args"] as? String ?? "",
                backend: cfg["backend"] as? String ?? "auto",
                installDir: game.installDir,
                retinaMode: cfg["retina_mode"] as? Bool ?? (NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false),
                metalHud: cfg["metal_hud"] as? Bool ?? false,
                esync: finalEsync,
                msync: msync,
                customEnv: cfg["custom_env"] as? String ?? ""
            )
            isLaunching = false
        }
    }

    private func loadCover() {
        guard let urlString = game.coverUrl,
              let url = URL(string: urlString) else { return }

        Task.detached(priority: .background) {
            do {
                let (data, _) = try await URLSession.shared.data(from: url)
                if let image = NSImage(data: data) {
                    await MainActor.run { coverImage = image }
                }
            } catch {
                // Cover not available, use placeholder
            }
        }
    }
}

// MARK: - Applications section

/// Installed Windows applications discovered in the bottle (Start Menu /
/// Program Files), shown below the games grid.
struct AppsSectionView: View {
    let apps: [WineApp]

    private let columns = [
        GridItem(.adaptive(minimum: 96, maximum: 120), spacing: 16)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Applications")
                .font(.headline)
                .padding(.horizontal, 24)

            LazyVGrid(columns: columns, spacing: 16) {
                ForEach(apps) { app in
                    AppCardView(app: app)
                }
            }
            .padding(.horizontal, 24)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct AppCardView: View {
    @EnvironmentObject var backend: BackendClient
    let app: WineApp
    @State private var isHovering = false
    @State private var isLaunching = false
    @State private var showUninstallConfirm = false
    // See GameCardView.isReachable for why this is cached rather than read
    // directly from `app` in body.
    @State private var isReachable = true

    private var icon: NSImage? {
        guard let b64 = app.iconBase64,
              let data = Data(base64Encoded: b64) else { return nil }
        return NSImage(data: data)
    }

    var body: some View {
        Button {
            launch()
        } label: {
            VStack(spacing: 8) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14)
                        .fill(.ultraThinMaterial)
                        .frame(width: 72, height: 72)
                    if let icon {
                        Image(nsImage: icon)
                            .resizable().interpolation(.high)
                            .scaledToFit()
                            .frame(width: 48, height: 48)
                    } else {
                        Image(systemName: "app.fill")
                            .font(.system(size: 28))
                            .foregroundStyle(.secondary)
                    }
                    if isLaunching {
                        LaunchingOverlay(cornerRadius: 14)
                            .frame(width: 72, height: 72)
                        ProgressView().controlSize(.small).tint(.white)
                    }
                }
                .overlay(
                    RoundedRectangle(cornerRadius: 14)
                        .strokeBorder(
                            isHovering ? Color.accentColor.opacity(0.5) : Color.primary.opacity(0.08),
                            lineWidth: 1
                        )
                )
                .overlay(alignment: .topLeading) {
                    if !isReachable {
                        Image(systemName: "externaldrive.badge.exclamationmark")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundStyle(.white)
                            .padding(4)
                            .background(.orange, in: Circle())
                            .padding(4)
                    }
                }
                .opacity(isReachable ? 1.0 : 0.6)
                .scaleEffect(isHovering ? 1.05 : 1.0)

                Text(app.name)
                    .font(.caption)
                    .lineLimit(2)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: .infinity)
            }
        }
        .buttonStyle(.plain)
        .animation(.easeOut(duration: 0.2), value: isHovering)
        .onHover { isHovering = $0 }
        .help(isReachable ? app.name : L("This app's files aren't available — its drive isn't connected."))
        .accessibilityLabel("Launch \(app.name)")
        .onAppear { isReachable = app.isReachable }
        .onChange(of: backend.volumeChangeTick) { _, _ in
            isReachable = app.isReachable
        }
        .contextMenu {
            Button("Launch") { launch() }
            Button("Show in Finder") {
                NSWorkspace.shared.selectFile(app.exe, inFileViewerRootedAtPath: "")
            }
            Divider()
            Button("Uninstall…", role: .destructive) { showUninstallConfirm = true }
        }
        .confirmationDialog(
            "Uninstall \(app.name)?",
            isPresented: $showUninstallConfirm,
            titleVisibility: .visible
        ) {
            Button("Uninstall", role: .destructive) { uninstall() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This opens the program's uninstaller. Follow its prompts to remove \(app.name) from this bottle.")
        }
    }

    private func launch() {
        guard let prefix = backend.activePrefix, !isLaunching, isReachable else { return }
        isLaunching = true
        Task {
            await backend.launchApp(prefix: prefix, app: app)
            isLaunching = false
        }
    }

    private func uninstall() {
        guard let prefix = backend.activePrefix else { return }
        Task {
            await backend.uninstallApp(prefix: prefix, app: app)
            // The uninstaller is interactive; rescan a few times so the app
            // disappears from the list once the user finishes removing it.
            for _ in 0..<20 {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard backend.activePrefix == prefix else { break }
                await backend.scanApps(prefix: prefix)
                if !backend.apps.contains(where: { $0.exe == app.exe }) { break }
            }
        }
    }
}
