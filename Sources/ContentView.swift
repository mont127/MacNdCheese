import SwiftUI

extension Notification.Name {
    /// Posted by the "New Bottle" menu command; ContentView listens for it.
    static let createNewBottle = Notification.Name("createNewBottle")
}

struct ContentView: View {
    @EnvironmentObject var backend: BackendClient
    @EnvironmentObject var announcements: AnnouncementChecker
    @EnvironmentObject var updateChecker: UpdateChecker
    @EnvironmentObject var loc: LocalizationManager
    @Environment(\.openSettings) private var openSettings
    @State private var searchText = ""
    @State private var showCreateBottle = false
    @State private var showAnnouncement = false
    @State private var showStore = false
    @State private var showEpicStore = false
    @State private var newBottleName = ""
    @State private var showKillConfirmation = false
    @State private var showOnboarding = false
    @State private var detailGame: Game?

    @ViewBuilder private var killWineserverButton: some View {
        Button {
            showKillConfirmation = true
        } label: {
            Image(systemName: "stop.circle").foregroundStyle(.red)
        }
        .help(L("Kill Wineserver"))
        .disabled(backend.activePrefix == nil)
    }

    @ViewBuilder private var settingsButtons: some View {
                Button { openSettings() } label: { Image(systemName: "gear") }
            .help(L("Settings"))
            .accessibilityLabel(L("Settings"))
        if announcements.hasNewAnnouncement {
            Button { showAnnouncement = true } label: {
                Image(systemName: "bell.badge.fill").symbolRenderingMode(.multicolor)
            }
            .help(L("New Announcement"))
            .accessibilityLabel(L("New Announcement"))
        }
        if activeBottle?.isEpicBottle == true && backend.epicAuthenticated {
            Button { showEpicStore = true } label: {
                Label(L("Open Store"), systemImage: "cart")
            }
            .help(L("Open Epic Games Store"))
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
        if showStore { return L("Store") }
        if let bottle = activeBottle { return bottle.name }
        return "MacNCheese"
    }

    private var detailSubtitle: String {
        if showStore || backend.activePrefix == nil { return "" }
        return L("Library")
    }

    private func openDetail(_ game: Game?) {
        withAnimation(.spring(response: 0.38, dampingFraction: 0.86)) {
            detailGame = game
        }
    }

    @ViewBuilder private var detailContent: some View {
        if showStore {
            StoreView(searchText: searchText)
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
            GameGridView(
                games: filteredGames,
                searchText: $searchText,
                onOpenDetail: { openDetail($0) }
            )
            .transition(.opacity)
        }
    }

    private var detailPane: some View {
        ZStack {
            Color.clear
            detailContent

            // In-pane game detail / launch page. Sits on top of the grid (which
            // stays mounted, so the toolbar is preserved) and animates in/out.
            // The sidebar is the other split column, untouched.
            if let game = detailGame {
                GameDetailView(game: game, onClose: { openDetail(nil) })
                    .transition(.opacity.combined(with: .scale(scale: 0.97, anchor: .center)))
                    .zIndex(3)
            }
        }
        .animation(.easeInOut(duration: 0.22), value: backend.activePrefix)
        .animation(.easeInOut(duration: 0.22), value: showStore)
        .animation(.easeInOut(duration: 0.22), value: backend.games.isEmpty)
        .background(Color(.windowBackgroundColor))
        .navigationTitle(detailTitle)
        .navigationSubtitle(detailSubtitle)
    }

    var body: some View {
        NavigationSplitView {
            SidebarView(showCreateBottle: $showCreateBottle, showStore: $showStore)
        } detail: {
            detailPane
        }
        .onChange(of: backend.activePrefix) { _, _ in showStore = false; detailGame = nil }
        .navigationSplitViewStyle(.balanced)
        .tint(.brand)   // brand accent for sidebar selection + prominent buttons
        .searchable(text: $searchText, placement: .toolbar, prompt: showStore ? L("Search showcase") : L("Search games"))
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
            ToolbarItem(placement: .navigation) {
                // Inside the Store there was no way back except the sidebar —
                // give it an explicit "Return to Library" button.
                if showStore {
                    Button {
                        showStore = false
                    } label: {
                        Label(L("Return to Library"), systemImage: "chevron.left")
                    }
                    .labelStyle(.titleAndIcon)
                    .help(L("Return to Library"))
                }
            }
            ToolbarItem(placement: .destructiveAction) {
                killWineserverButton
            }
            ToolbarItemGroup(placement: .primaryAction) {
                settingsButtons
            }
        }
        .alert(L("Kill Wineserver?"), isPresented: $showKillConfirmation) {
            Button(L("Kill"), role: .destructive) {
                guard let prefix = backend.activePrefix else { return }
                Task { await backend.killWineserver(prefix: prefix) }
            }
            Button(L("Cancel"), role: .cancel) {}
        } message: {
            Text(L("This will forcefully terminate all Wine processes in the current bottle. Any unsaved game progress will be lost."))
        }
        .safeAreaInset(edge: .top) {
            AppUpdateBanner()
        }
        // Re-render the entire main UI when the language changes (Settings is a
        // separate scene, so its window is unaffected). Switching is live.
        .id(loc.language)
        // First-launch language popup (also reachable later via Settings → Language).
        .sheet(isPresented: $loc.needsChoice) {
            LanguagePickerSheet()
        }
        // First-run onboarding installer — shown automatically after the language
        // is chosen, so a new user gets a working Wine + graphics stack without
        // ever opening Settings. Lives in its own modifier to keep this body's
        // type-checking light.
        .modifier(OnboardingPresenter(show: $showOnboarding))
    }
}



/// Owns first-run onboarding presentation so ContentView's already-large body
/// stays light to type-check. Shows OnboardingView once the language is chosen
/// and the backend reports Wine is missing; existing installs are marked
/// complete silently so an upgrader is never nagged. Any dismissal (button,
/// Escape, click-out) marks it complete so it won't reappear.
private struct OnboardingPresenter: ViewModifier {
    @EnvironmentObject var backend: BackendClient
    @EnvironmentObject var loc: LocalizationManager
    @AppStorage(OnboardingView.completeKey) private var onboardingComplete = false
    @Binding var show: Bool

    func body(content: Content) -> some View {
        content
            .sheet(isPresented: $show, onDismiss: {
                onboardingComplete = true
                MacNCheeseSupport.markInitialized()
            }) {
                OnboardingView()
            }
            .onAppear { evaluate() }
            .onChange(of: backend.isConnected) { _, connected in
                if connected { evaluate() }
            }
            .onChange(of: loc.needsChoice) { _, needs in
                if !needs { evaluate() }
            }
    }

    private func evaluate() {
        guard !show, !loc.needsChoice else { return }

        // First launch is keyed on MacNCheese's Application Support folder, not
        // just a UserDefaults flag. If the user deleted it (a clean reset) or
        // this is a genuine first run, re-onboard even if a stale "complete"
        // flag survived in UserDefaults.
        if !MacNCheeseSupport.exists {
            onboardingComplete = false
            // Defer a tick so we don't present while the language sheet (bound to
            // the same view) is still dismissing.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                if !MacNCheeseSupport.exists { show = true }
            }
            return
        }

        // Folder exists: respect the completed/skipped flag, but if onboarding
        // was never finished and Wine still isn't installed, offer it.
        guard !onboardingComplete else { return }
        Task {
            // nil = backend not ready yet; a later isConnected change retries.
            guard let status = await backend.getComponentsStatus() else { return }
            if status.hasWine {
                onboardingComplete = true
            } else {
                show = true
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
                .foregroundStyle(Color.brand.opacity(0.8))
                .padding(.bottom, 8)

            Text(customExeName?.uppercased() ?? "STEAM")
                .font(.system(.largeTitle, design: .default).weight(.bold))
                .tracking(4)
                .foregroundStyle(.primary)

            Text(customExeName != nil ? L("Launch to browse your games.") : L("Launch Steam to browse and install games."))
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
                    Text(backend.steamRunning ? String(format: L("Close %@"), customExeName ?? "Steam") : L("Launch"))
                        .fontWeight(.bold)
                }
                .frame(width: 160, height: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(backend.steamRunning ? .red : Color.brand)
            .controlSize(.large)
            .disabled(backend.activePrefix == nil || isLaunching)

            Spacer().frame(height: 32)

            HStack(spacing: 12) {
                Button(L("Run Installer")) {
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

                Button(L("Add Game")) {
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
        panel.title = L("Select Game EXE")
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
                .foregroundStyle(Color.brand.opacity(0.8))
            Text(L("No bottle selected"))
                .font(.title)
                .fontWeight(.bold)
            Text(L("Create a bottle to get started."))
                .foregroundStyle(.secondary)
            Button(L("Create Bottle")) {
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
                .foregroundStyle(Color.brand.opacity(0.8))
                .padding(.bottom, 12)
            Text(L("No Games"))
                .font(.title)
                .fontWeight(.bold)
            Text(L("Add a game or run an installer to get started."))
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
                        Text(backend.steamRunning ? String(format: L("Close %@"), launcherName) : String(format: L("Launch %@"), launcherName))
                            .fontWeight(.bold)
                    }
                    .frame(minWidth: 160)
                }
                .buttonStyle(.borderedProminent)
                .tint(backend.steamRunning ? .red : Color.brand)
                .controlSize(.large)
                .disabled(isLaunching)
                Spacer().frame(height: 20)
            }
            HStack(spacing: 12) {
                Button(L("Run Installer")) {
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

                Button(L("Add Game")) {
                    addManualGame()
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
            }
            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .alert(L("Compatibility List Update"), isPresented: $showCompatibilityListNotice) {
            Button(L("Launch")) {
                guard let prefix = backend.activePrefix else { return }
                launchLauncher(prefix: prefix)
            }
            Button(L("Cancel"), role: .cancel) {}
        } message: {
            Text(L("The compatibility list may have changed. Launch the launcher anyway?"))
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
        panel.title = L("Select Game EXE")
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
                .help(L("Dismiss"))
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