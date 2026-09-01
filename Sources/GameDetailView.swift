import SwiftUI
import UniformTypeIdentifiers

/// In-pane game detail / launch page. Replaces the old modal GameLaunchSheet for
/// the Steam grid: clicking a game animates this view into the content area
/// (sidebar + toolbar stay put). Layout — a wide Steam hero banner on top, the
/// game name + green Launch button, the Steam description, and a "Wine &
/// Graphics" options panel on the right. Launch logic is the same as the old
/// sheet (Steam mode, backend, sync, env, debug…).
struct GameDetailView: View {
    @EnvironmentObject var backend: BackendClient
    let game: Game
    var coverImage: NSImage?
    var onClose: () -> Void

    @State private var selectedExe: String = ""
    @State private var detectedExes: [BackendClient.DetectedExe] = []
    @State private var extraArgs = ""
    @State private var isLaunching = false
    @State private var loadingExes = true
    @State private var selectedBackend: String = "auto"
    @State private var availableBackends: [GraphicsBackend] = []
    @State private var loadingBackends = true
    @State private var retinaMode: Bool = NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false
    @State private var metalHud: Bool = false
    @State private var dpiAware: Bool = false
    @State private var gameMode: Bool = true
    @State private var advancedDebug: Bool = false
    @State private var enableEsync: Bool = true
    // x87 JIT acceleration (rosettax87). Master switch is on by default;
    // the tuning flags are opt-in and only shown while it is enabled.
    // ntcore-style 4GB patch: sets IMAGE_FILE_LARGE_ADDRESS_AWARE on a 32-bit
    // exe that lacks it. Backend default is on; it edits the exe and keeps a
    // .mnc-orig backup, so it is worth surfacing rather than doing silently.
    // nil = unknown (not a PE / unreadable). Only HIDE the 32-bit-only
    // settings when we positively know the exe is 64-bit.
    @State private var exeIs32Bit: Bool? = nil
    @State private var largeAddressAware: Bool = true
    @State private var x87Jit: Bool = true
    @State private var x87ExtendedFpr: Bool = false
    @State private var x87FastRound: Bool = false
    @State private var x87F32Arith: Bool = false
    @State private var x87FastRecipDiv: Bool = false
    @State private var enableMsync: Bool = true
    @State private var advertiseAVX: Bool = false
    @State private var customEnv: String = ""
    @State private var steamMode: String = "silent"
    @State private var steamDescription: String?
    @State private var loadingSteamDescription = false
    @State private var bannerImage: NSImage?
    @State private var screenshots: [String] = []   // full-size URLs
    @State private var thumbnails: [String] = []     // thumbnail URLs
    @State private var fullScreenshot: String?       // tapped screenshot → viewer

    /// One row of the EXE menu. Steam's description leads, with the actual file
    /// name after it so the choice is never ambiguous; a bare executable shows
    /// just its name.
    @ViewBuilder
    private func exeMenuRow(_ e: BackendClient.DetectedExe) -> some View {
        let name = (e.path as NSString).lastPathComponent
        Button {
            selectedExe = e.path
            Task { await refreshExeArch() }
        } label: {
            Text(e.label.isEmpty ? name : "\(e.label)  (\(name))")
        }
    }

    /// What the collapsed EXE button shows: Steam's description when we have one,
    /// otherwise the bare filename. The filename is still visible in the open list.
    private var selectedExeDisplay: String {
        guard !effectiveExe.isEmpty else { return L("Select…") }
        if let hit = detectedExes.first(where: { $0.path == effectiveExe }), !hit.label.isEmpty {
            return hit.label
        }
        return (effectiveExe as NSString).lastPathComponent
    }

    private var effectiveExe: String {
        if !selectedExe.isEmpty { return selectedExe }
        return game.exe ?? ""
    }

    private var launchingLabel: String {
        steamMode == "none" ? L("Launching…") : L("Starting Steam…")
    }

    // MARK: - Body

    var body: some View {
        ZStack(alignment: .topLeading) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    banner

                    HStack(alignment: .top, spacing: 20) {
                        VStack(alignment: .leading, spacing: 14) {
                            Text(game.name)
                                .font(.largeTitle).fontWeight(.bold)
                                .lineLimit(2)
                            if !game.isManual {
                                Text(String(format: L("App ID: %@"), game.appid))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            launchButton
                            descriptionSection
                            Spacer(minLength: 0)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)

                        wineStuffPanel
                            .frame(width: 340)
                    }

                    screenshotsSection
                }
                .padding(24)
                .padding(.top, 8)
            }

            backButton
                .padding(16)

            screenshotViewer
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.windowBackgroundColor))
        .task {
            await loadBanner()
            await loadExes()
            await loadBackends()
            await loadBottleDefaults()
            await loadGameConfig()
            await refreshExeArch()
            await loadSteamMedia()
        }
    }

    // MARK: - Screenshots showcase (pulled from Steam)

    @ViewBuilder
    private var screenshotsSection: some View {
        if !thumbnails.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text(L("Screenshots")).font(.headline)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 10) {
                        ForEach(Array(thumbnails.enumerated()), id: \.offset) { idx, thumb in
                            Button {
                                let full = idx < screenshots.count ? screenshots[idx] : thumb
                                withAnimation(.easeInOut(duration: 0.2)) { fullScreenshot = full }
                            } label: {
                                AsyncImage(url: URL(string: thumb)) { phase in
                                    if let img = phase.image {
                                        img.resizable().aspectRatio(contentMode: .fill)
                                    } else {
                                        Rectangle().fill(.ultraThinMaterial)
                                    }
                                }
                                .frame(width: 240, height: 135)
                                .clipShape(RoundedRectangle(cornerRadius: 9))
                                .overlay(RoundedRectangle(cornerRadius: 9).strokeBorder(.white.opacity(0.08)))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    .padding(.bottom, 4)
                }
            }
        }
    }

    @ViewBuilder
    private var screenshotViewer: some View {
        if let full = fullScreenshot {
            ZStack {
                Color.black.opacity(0.9)
                    .ignoresSafeArea()
                    .onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { fullScreenshot = nil } }
                AsyncImage(url: URL(string: full)) { phase in
                    if let img = phase.image {
                        img.resizable().scaledToFit()
                    } else {
                        ProgressView().controlSize(.large).tint(.white)
                    }
                }
                .padding(40)
                VStack {
                    HStack {
                        Spacer()
                        Button { withAnimation(.easeInOut(duration: 0.2)) { fullScreenshot = nil } } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.title).foregroundStyle(.white.opacity(0.85))
                        }
                        .buttonStyle(.plain)
                        .padding(18)
                    }
                    Spacer()
                }
            }
            .transition(.opacity)
            .zIndex(20)
        }
    }

    // MARK: - Hero banner

    private var banner: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 16)
                .fill(LinearGradient(
                    colors: [Color.wineDeep.opacity(0.55), Color.bg2Fallback],
                    startPoint: .topLeading, endPoint: .bottomTrailing))
            if let img = bannerImage {
                Image(nsImage: img)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
            } else if let cover = coverImage {
                Image(nsImage: cover)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .blur(radius: 18)
                    .overlay(Color.black.opacity(0.25))
            } else {
                Image(systemName: "gamecontroller.fill")
                    .font(.system(size: 46))
                    .foregroundStyle(.white.opacity(0.5))
            }
        }
        .frame(height: 230)
        .frame(maxWidth: .infinity)
        .clipShape(RoundedRectangle(cornerRadius: 16))
        .overlay(
            RoundedRectangle(cornerRadius: 16)
                .strokeBorder(.white.opacity(0.08), lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.35), radius: 14, y: 6)
    }

    // MARK: - Left column

    private var launchButton: some View {
        Button {
            launchGame()
        } label: {
            HStack(spacing: 8) {
                if isLaunching {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: "play.fill")
                }
                Text(isLaunching ? launchingLabel : L("Launch game"))
                    .fontWeight(.bold)
            }
            .frame(minWidth: 150)
        }
        .buttonStyle(.borderedProminent)
        .tint(.green)
        .controlSize(.large)
        .disabled((game.epicAppName == nil && game.amazonId == nil && effectiveExe.isEmpty) || isLaunching || !game.isReachable)
        .help(game.isReachable ? "" : L("This game's files aren't available — its drive isn't connected."))
    }

    @ViewBuilder
    private var descriptionSection: some View {
        if !game.isManual {
            VStack(alignment: .leading, spacing: 6) {
                Text(L("Description of the game"))
                    .font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
                if loadingSteamDescription {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text(L("Loading from Steam...")).font(.caption).foregroundStyle(.secondary)
                    }
                } else if let steamDescription, !steamDescription.isEmpty {
                    Text(steamDescription)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .lineSpacing(2)
                        .textSelection(.enabled)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text(L("No Steam description available."))
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(.top, 4)
        }
    }

    // MARK: - Right column ("Wine stuff")

    private var wineStuffPanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            Label(L("Wine & Graphics"), systemImage: "wineglass")
                .font(.headline)
            Divider()
            exeSection
            graphicsSection
            argsSection
            retinaSection
            dpiAwareSection
            metalHudToggle
            gameModeToggle
            advancedDebugToggle
            synchronizationSection
            // The 4GB patch and the x87 JIT are both 32-bit-only: 64-bit PEs are
            // large-address-aware by definition, and wine only engages the x87
            // loader for a 32-bit image. Showing them for a 64-bit game would
            // offer switches that cannot do anything.
            // 4GB patch: 32-bit only (64-bit PEs are large-address-aware already),
            // but it applies on any Mac. x87 JIT: 32-bit AND Apple Silicon only.
            if exeIs32Bit != false {
                largeAddressSection
                if isAppleSilicon { x87Section }
            }
            environmentSection
        }
        .padding(16)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(.white.opacity(0.08), lineWidth: 1)
        )
    }

    private var backButton: some View {
        Button { onClose() } label: {
            HStack(spacing: 5) {
                Image(systemName: "chevron.left").font(.system(size: 12, weight: .bold))
                Text(L("Library")).fontWeight(.medium)
            }
            .padding(.vertical, 7).padding(.horizontal, 12)
            .background(.ultraThinMaterial, in: Capsule())
            .overlay(Capsule().strokeBorder(.white.opacity(0.12)))
        }
        .buttonStyle(.plain)
        .keyboardShortcut(.cancelAction)
        .help(L("Return to Library"))
    }

    // MARK: - Option sub-views (ported from the old launch sheet)

    private var exeSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L("EXE:")).font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
            if loadingExes {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(L("Scanning...")).font(.caption).foregroundStyle(.secondary)
                }
            } else {
                // A Menu rather than a Picker so the collapsed button can show the
                // friendly name alone while the open list stays explicit about
                // which file each entry is. macOS flattens a menu item's layout to
                // one line, so the filename goes IN the row text rather than under it.
                Menu {
                    let official = detectedExes.filter { !$0.label.isEmpty }
                    let others   = detectedExes.filter { $0.label.isEmpty }
                    if !official.isEmpty {
                        Section(L("Official game launchers")) {
                            ForEach(official) { e in exeMenuRow(e) }
                        }
                    }
                    if !others.isEmpty {
                        Section(official.isEmpty ? L("Executables") : L("Other executables")) {
                            ForEach(others) { e in exeMenuRow(e) }
                        }
                    }
                } label: {
                    Text(selectedExeDisplay)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .foregroundStyle(.primary)   // not the app tint: this is a value, not an action
                }
                .menuStyle(.borderlessButton)
                .tint(.primary)   // control tint, not text colour: .foregroundStyle
                                  // loses to the accent colour on a borderless menu
                .fixedSize()

                Button(L("Browse…")) { browseExe() }
                    .buttonStyle(.bordered).controlSize(.small)
            }
        }
    }

    private var graphicsSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L("Graphics Engine:")).font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
            if loadingBackends {
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(L("Detecting...")).font(.caption).foregroundStyle(.secondary)
                }
            } else {
                let mainIds: [String] = ["auto", "wine_devel", "opengl", "dxmt", "vr", "d3dmetal3", "dxvk", "vkd3d-proton"]
                let experimentalIds: [String] = ["wine", "gptk_full"]
                let mainBackends = availableBackends.filter { mainIds.contains($0.backendId) }
                    .sorted { mainIds.firstIndex(of: $0.backendId) ?? 99 < mainIds.firstIndex(of: $1.backendId) ?? 99 }
                let experimentalBackends = availableBackends.filter { experimentalIds.contains($0.backendId) }
                    .sorted { experimentalIds.firstIndex(of: $0.backendId) ?? 99 < experimentalIds.firstIndex(of: $1.backendId) ?? 99 }
                Picker("", selection: $selectedBackend) {
                    ForEach(mainBackends) { b in Text(engineLabel(b)).tag(b.backendId) }
                    if !experimentalBackends.isEmpty {
                        Divider()
                        Text(L("— Experimental —")).tag("__sep__").disabled(true)
                        ForEach(experimentalBackends) { b in Text(engineLabel(b)).tag(b.backendId) }
                    }
                }
                .labelsHidden()
            }
        }
    }

    private var argsSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(L("Args:")).font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
            TextField(L("Optional launch arguments…"), text: $extraArgs)
                .textFieldStyle(.roundedBorder)
        }
    }

    private var retinaSection: some View {
        Toggle(isOn: $retinaMode) {
            Text(L("Retina hi-res mode")).font(.caption).fontWeight(.semibold)
        }
    }

    /// Same rule the backend applies (_game_needs_dpi_aware): the mismatch only exists on
    /// a HiDPI panel, and only these titles are known to trip over it. Kept in sync with
    /// _DPI_AWARE_EXES / _DPI_AWARE_TITLE_HINTS in backend_server.py, which stays the
    /// source of truth for launches that don't come from this sheet.
    private var defaultDpiAware: Bool {
        let hidpi = NSScreen.main.map { $0.backingScaleFactor > 1.0 } ?? false
        guard hidpi else { return false }
        let hay = (game.name + " " + selectedExe).lowercased()
        return hay.contains("battlefield 4") || hay.contains("bf4.exe")
    }

    private var dpiAwareSection: some View {
        VStack(alignment: .leading, spacing: 2) {
            Toggle(isOn: $dpiAware) {
                Text(L("HiDPI coordinate fix")).font(.caption).fontWeight(.semibold)
            }
            Text(L("On a Retina display Wine reports the screen size and the display-mode list in different units, so some fullscreen games mis-place the mouse or show a black window. Enabled automatically for games known to need it."))
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var metalHudToggle: some View {
        Toggle(isOn: $metalHud) {
            Text(L("Metal HUD")).font(.caption).fontWeight(.semibold)
        }
    }

    private var gameModeToggle: some View {
        VStack(alignment: .leading, spacing: 2) {
            Toggle(isOn: $gameMode) {
                Text(L("Game Mode")).font(.caption).fontWeight(.semibold)
            }
            Text(L("Forces macOS Game Mode on while the game runs (prioritizes CPU/GPU and reduces input latency). macOS can't auto-detect Wine games as games, so we enable it for you."))
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var advancedDebugToggle: some View {
        Toggle(isOn: $advancedDebug) {
            Text(L("Advanced debug (verbose logs)")).font(.caption).fontWeight(.semibold)
        }
    }

    private var synchronizationSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle(isOn: $enableEsync) { Text(L("Enable ESync")).font(.caption).fontWeight(.semibold) }
            Toggle(isOn: $enableMsync) { Text(L("Enable MSync")).font(.caption).fontWeight(.semibold) }
            Text("STEAM").font(.caption).fontWeight(.semibold).foregroundStyle(.secondary).padding(.top, 2)
            Picker("", selection: $steamMode) {
                Text(L("Silent Steam")).tag("silent")
                Text(L("Open Steam")).tag("open")
                Text(L("No Steam")).tag("none")
            }
            .pickerStyle(.segmented)
            .labelsHidden()
        }
    }

    private var largeAddressSection: some View {
        VStack(alignment: .leading, spacing: 2) {
            Toggle(isOn: $largeAddressAware) {
                Text(L("4GB patch (large address aware)")).font(.caption).fontWeight(.semibold)
            }
            Text(L("Lets a 32-bit game use the full 4GB Wine offers instead of being capped at 2GB — some titles simply run out and crash without it. Edits the game's .exe, keeping the original alongside as .mnc-orig. No effect on 64-bit games."))
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// x87 JIT acceleration only exists on Apple Silicon -- there is no Rosetta to
    /// patch on an Intel Mac, and the backend's _rosetta_x87_loader() returns None
    /// there. Asking about hw.optional.arm64 rather than the build architecture is
    /// deliberate: the machine is what matters, and the app may itself be running
    /// translated.
    private var isAppleSilicon: Bool {
        var value: Int32 = 0
        var size = MemoryLayout<Int32>.size
        if sysctlbyname("hw.optional.arm64", &value, &size, nil, 0) == 0 { return value == 1 }
        return false
    }

    private var x87Section: some View {
        VStack(alignment: .leading, spacing: 6) {
            VStack(alignment: .leading, spacing: 2) {
                Toggle(isOn: $x87Jit) {
                    Text(L("x87 JIT acceleration")).font(.caption).fontWeight(.semibold)
                }
                Text(L("Speeds up 32-bit games that do their float maths on the x87 stack (most Delphi/Pascal-era titles). Turn off if a game misbehaves."))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if x87Jit {
                VStack(alignment: .leading, spacing: 6) {
                    x87Option($x87ExtendedFpr,
                              L("Extended register pool"),
                              L("Uses 16 scratch FP registers instead of 8. Safe: does not change results."))
                    x87Option($x87FastRound,
                              L("Fast rounding"),
                              L("Skips rounding-mode dispatch. Safe only for games that stay on round-to-nearest."))
                    x87Option($x87F32Arith,
                              L("32-bit arithmetic chains"),
                              L("Keeps single-precision chains in 32-bit (also enables narrowing, which it depends on). Not bit-exact; upstream turned both off by default after game misbehaviour."))
                    x87Option($x87FastRecipDiv,
                              L("Fast reciprocal divide"),
                              L("Turns division by a constant into a multiply. Up to 1 ULP off; can break convergence loops."))
                }
                .padding(.leading, 14)
            }
        }
    }

    @ViewBuilder
    private func x87Option(_ isOn: Binding<Bool>, _ title: String, _ caption: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Toggle(isOn: isOn) { Text(title).font(.caption).fontWeight(.semibold) }
            Text(caption)
                .font(.caption2)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var environmentSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(L("Environment Variables:")).font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
            Toggle(isOn: $advertiseAVX) {
                Text(L("Advertise AVX2 / FMA / F16C")).font(.caption).fontWeight(.semibold)
            }
            ZStack(alignment: .topLeading) {
                if customEnv.isEmpty {
                    Text("DXVK_ASYNC=1")
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.tertiary)
                        .padding(.horizontal, 5).padding(.vertical, 8)
                        .allowsHitTesting(false)
                }
                TextEditor(text: $customEnv)
                    .font(.system(.caption, design: .monospaced))
                    .frame(minHeight: 44, maxHeight: 64)
                    .hideScrollBackgroundCompat()
                    .background(.black.opacity(0.2))
                    .clipShape(RoundedRectangle(cornerRadius: 6))
            }
        }
    }

    // MARK: - Config load / save (ported)

    private func loadGameConfig() async {
        guard let prefix = backend.activePrefix else { return }
        let cfg = await backend.getGameConfig(prefix: prefix, appid: game.appid)
        if let exe = cfg["exe"] as? String, !exe.isEmpty { selectedExe = exe }
        if let b = cfg["backend"] as? String { selectedBackend = b }
        if let a = cfg["args"] as? String { extraArgs = a }
        if let r = cfg["retina_mode"] as? Bool { retinaMode = r }
        if let h = cfg["metal_hud"] as? Bool { metalHud = h }
        dpiAware = cfg["dpi_aware"] as? Bool ?? defaultDpiAware
        if let gm = cfg["game_mode"] as? Bool { gameMode = gm }
        if let d = cfg["debug"] as? Bool { advancedDebug = d }
        if let e = cfg["esync"] as? Bool { enableEsync = e }
        if let m = cfg["msync"] as? Bool { enableMsync = m }
        if let env = cfg["custom_env"] as? String { customEnv = env }
        if let avx = cfg["rosetta_avx"] as? Bool { advertiseAVX = avx }
        if let v = cfg["large_address_aware"] as? Bool { largeAddressAware = v }
        if let v = cfg["x87_jit"] as? Bool { x87Jit = v }
        if let v = cfg["x87_extended_fpr"] as? Bool { x87ExtendedFpr = v }
        if let v = cfg["x87_fast_round"] as? Bool { x87FastRound = v }
        if let v = cfg["x87_f32_arith"] as? Bool { x87F32Arith = v }
        if let v = cfg["x87_fast_recip_div"] as? Bool { x87FastRecipDiv = v }
        if let sm = cfg["steam_mode"] as? String { steamMode = sm }
    }

    private func saveGameConfig() async {
        guard let prefix = backend.activePrefix else { return }
        let sync = normalizedSyncSelection()
        await backend.setGameConfig(prefix: prefix, appid: game.appid, values: [
            "exe": selectedExe, "backend": selectedBackend, "args": extraArgs,
            "retina_mode": retinaMode, "metal_hud": metalHud, "game_mode": gameMode, "debug": advancedDebug,
            "esync": sync.esync, "msync": sync.msync, "custom_env": customEnv,
            "rosetta_avx": advertiseAVX, "steam_mode": steamMode,
            "large_address_aware": largeAddressAware,
            "x87_jit": x87Jit, "x87_extended_fpr": x87ExtendedFpr,
            "x87_fast_round": x87FastRound, "x87_f32_arith": x87F32Arith,
            "x87_fast_recip_div": x87FastRecipDiv,
            "dpi_aware": dpiAware,
        ])
        // Written separately: "auto" must leave the key ABSENT so the backend's
        // per-title detection still runs. Sending false would silently disable it.
        await backend.setBottleConfig(path: prefix, values: [
            "rosetta_avx": advertiseAVX, "custom_env": customEnv,
        ])
    }

    private func loadExes() async {
        loadingExes = true
        detectedExes = await backend.detectExesLabeled(
            installDir: game.installDir,
            prefix: backend.activePrefix ?? "",
            steamAppId: game.appid)
        // No "Auto-detect" row any more, so something must be selected. Order of
        // preference:
        //   1. game.exe  -- MNC's own scanned choice for this title. It is not a
        //      guess and it can differ from the obvious pick: OMSI 2 must start
        //      via Launcher.exe, while Omsi.exe alone renders a black window.
        //      Ranking used to be irrelevant here because an empty selection fell
        //      through to game.exe; now that the row is gone it has to be explicit.
        //   2. the first ranked candidate (Steam's launch list, else the heuristic).
        if selectedExe.isEmpty {
            if let known = game.exe, !known.isEmpty {
                selectedExe = known
                if !detectedExes.contains(where: { $0.path == known }) {
                    detectedExes.insert(.init(path: known, label: "", is32: nil), at: 0)
                }
            } else if let first = detectedExes.first {
                selectedExe = first.path
            }
        }
        loadingExes = false
    }

    /// Resolve whether the exe we would actually launch is 32-bit, so the
    /// 32-bit-only settings can hide themselves for a 64-bit title.
    private func refreshExeArch() async {
        let exe = effectiveExe
        guard !exe.isEmpty else { exeIs32Bit = nil; return }
        exeIs32Bit = await backend.exeIs32Bit(exe: exe)
    }

    private func loadBackends() async {
        loadingBackends = true
        if let response = await backend.listBackends() {
            availableBackends = response.backends.filter { $0.available }
            selectedBackend = "auto"
        }
        loadingBackends = false
    }

    private func loadBottleDefaults() async {
        guard let prefix = backend.activePrefix,
              let config = await backend.getBottleConfig(path: prefix) else { return }
        metalHud = config["metal_hud"] as? Bool ?? false
        advertiseAVX = config["rosetta_avx"] as? Bool ?? false
        customEnv = config["custom_env"] as? String ?? ""
    }

    private func loadSteamMedia() async {
        guard !game.isManual else { steamDescription = nil; loadingSteamDescription = false; return }
        loadingSteamDescription = true
        if let media = await backend.getSteamMedia(appid: game.appid) {
            steamDescription = media.description
            screenshots = media.screenshots
            thumbnails = media.thumbnails
        }
        loadingSteamDescription = false
    }

    /// Steam-CDN banner: try the wide library hero, then the header. Non-Steam
    /// games keep the blurred-cover / placeholder fallback in `banner`.
    private func loadBanner() async {
        guard !game.isManual, !game.appid.isEmpty, game.appid.allSatisfy(\.isNumber) else { return }
        let candidates = [
            "https://steamcdn-a.akamaihd.net/steam/apps/\(game.appid)/library_hero.jpg",
            "https://steamcdn-a.akamaihd.net/steam/apps/\(game.appid)/header.jpg",
        ]
        for str in candidates {
            guard let url = URL(string: str) else { continue }
            if let (data, resp) = try? await URLSession.shared.data(from: url),
               (resp as? HTTPURLResponse)?.statusCode == 200,
               let img = NSImage(data: data) {
                bannerImage = img
                return
            }
        }
    }

    private func normalizedSyncSelection() -> (esync: Bool, msync: Bool) {
        if enableMsync { return (false, true) }
        return (enableEsync, false)
    }

    private func combinedCustomEnv() -> String {
        var lines: [String] = []
        if advertiseAVX { lines.append("ROSETTA_ADVERTISE_AVX=1") }
        let extra = customEnv.trimmingCharacters(in: .whitespacesAndNewlines)
        if !extra.isEmpty { lines.append(extra) }
        return lines.joined(separator: "\n")
    }

    private func launchGame() {
        guard let prefix = backend.activePrefix, game.isReachable else { return }
        isLaunching = true
        let env = combinedCustomEnv()
        Task {
            await saveGameConfig()
            let sync = normalizedSyncSelection()
            if let appName = game.epicAppName {
                await backend.epicLaunchGame(
                    prefix: prefix, appName: appName, backend: selectedBackend,
                    retinaMode: retinaMode, metalHud: metalHud, gameMode: gameMode,
                    esync: sync.esync, msync: sync.msync, customEnv: env, debug: advancedDebug,
                    dpiAware: dpiAware)
            } else if let amazonId = game.amazonId {
                await backend.amazonLaunchGame(
                    prefix: prefix, amazonId: amazonId, backend: selectedBackend,
                    retinaMode: retinaMode, metalHud: metalHud, gameMode: gameMode,
                    esync: sync.esync, msync: sync.msync, customEnv: env, debug: advancedDebug,
                    dpiAware: dpiAware)
            } else {
                let exe = effectiveExe
                guard !exe.isEmpty else { isLaunching = false; return }
                await backend.launchGame(
                    prefix: prefix, exe: exe, args: extraArgs, backend: selectedBackend,
                    installDir: game.installDir, retinaMode: retinaMode, metalHud: metalHud, gameMode: gameMode,
                    esync: sync.esync, msync: sync.msync, gameName: game.name,
                    steamAppId: game.appid, steamMode: steamMode, customEnv: env, debug: advancedDebug,
                    dpiAware: dpiAware,
                    largeAddressAware: largeAddressAware,
                    x87Jit: x87Jit, x87ExtendedFpr: x87ExtendedFpr, x87FastRound: x87FastRound,
                    x87F32Arith: x87F32Arith, x87FastRecipDiv: x87FastRecipDiv)
            }
            isLaunching = false
            onClose()
        }
    }

    private func browseExe() {
        let panel = NSOpenPanel()
        var types: [UTType] = [.exe]
        if let msi = UTType(filenameExtension: "msi") { types.append(msi) }
        panel.allowedContentTypes = types
        panel.canChooseFiles = true
        if !game.installDir.isEmpty { panel.directoryURL = URL(fileURLWithPath: game.installDir) }
        if panel.runModal() == .OK, let url = panel.url {
            let path = url.path
            if !detectedExes.contains(where: { $0.path == path }) {
                detectedExes.insert(.init(path: path, label: "", is32: nil), at: 0)
            }
            selectedExe = path
            Task { await refreshExeArch() }
        }
    }

    private func engineLabel(_ b: GraphicsBackend) -> String {
        switch b.backendId {
        case "auto":          return L("Auto (recommended)")
        case "wine_devel", "opengl": return L("OpenGL (SDL3 / GL 3.2)")
        case "dxmt":          return L("DXMT (Balanced)")
        case "vr", "dxmt_openxr": return L("VR (OpenXR)")
        case "d3dmetal3":     return L("D3DMetal (Best Performance)")
        case "dxvk":          return L("DXVK (Best Compatibility)")
        case "vkd3d-proton":  return L("VKD3D-Proton (D3D12)")
        case "wine":          return L("Wine Builtin")
        case "gptk":          return L("GPTK (D3DMetal, copy DLLs)")
        case "gptk_full":     return L("GPTK Full (Apple Toolkit)")
        default:              return b.label
        }
    }

    private func abbreviateExe(_ path: String) -> String {
        let installDir = game.installDir
        if !installDir.isEmpty, path.hasPrefix(installDir) {
            let relative = String(path.dropFirst(installDir.count))
            return relative.hasPrefix("/") ? String(relative.dropFirst()) : relative
        }
        return URL(fileURLWithPath: path).lastPathComponent
    }
}

private extension Color {
    /// Dark slate used behind the hero banner before the image loads.
    static let bg2Fallback = Color(red: 0.09, green: 0.11, blue: 0.15)
}
