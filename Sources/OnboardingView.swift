import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// MacNCheese's Application Support folder. Its existence is the canonical
/// "has this install been set up before?" signal — the backend installs Wine and
/// everything else under it (`…/MacNCheese/deps`). Deleting the folder is a clean
/// reset: the next launch behaves like a first launch and re-runs onboarding.
/// (Nothing creates it at startup, so an empty/missing folder genuinely means
/// "never set up".)
enum MacNCheeseSupport {
    static var directory: String {
        NSHomeDirectory() + "/Library/Application Support/MacNCheese"
    }
    static var exists: Bool {
        FileManager.default.fileExists(atPath: directory)
    }
    /// Record that the app has launched/onboarded before, so onboarding won't
    /// reappear until the folder is deleted. A real install already creates the
    /// folder via `deps/`; this also covers the "Skip for now" case.
    static func markInitialized() {
        try? FileManager.default.createDirectory(
            atPath: directory, withIntermediateDirectories: true)
    }
}

/// First-run welcome + one-click installer. Shown automatically on first launch
/// (after the language pick) so a new user can get a working Wine + graphics
/// stack without ever opening Settings → Setup. The Setup tab stays available
/// for advanced/manual control later.
struct OnboardingView: View {
    @EnvironmentObject var backend: BackendClient
    @Environment(\.dismiss) private var dismiss
    @StateObject private var installer = InstallRunner()

    @AppStorage(OnboardingView.completeKey) private var onboardingComplete = false

    @State private var status: ComponentsStatus?
    @State private var loadingStatus = true
    @State private var installEverything = false
    @State private var started = false
    // Steam guide (shown AFTER components are installed).
    @State private var inSteamStep = false
    @State private var steamBusy = false
    @State private var steamStarted = false

    /// UserDefaults flag: set once the user finishes (or skips) onboarding so it
    /// never auto-appears again. ContentView reads it to decide whether to show.
    static let completeKey = "mnc_onboarding_complete"

    // Essentials = the minimum needed to actually run a game. Wine D3DMetal is
    // part of the baseline (the high-performance launch engine).
    private var essentialsInstalled: Bool {
        guard let s = status else { return false }
        return s.hasTools && s.hasWineStable && s.hasWineD3DMetal && s.hasDxvk64 && s.hasMesa
    }

    var body: some View {
        VStack(spacing: 0) {
            content
        }
        .frame(width: 520, height: 600)
        .background(.ultraThinMaterial)
        .interactiveDismissDisabled(installer.isRunning || steamBusy)
        .onAppear(perform: loadStatus)
    }

    @ViewBuilder private var content: some View {
        if loadingStatus {
            loadingView
        } else if inSteamStep {
            steamView
        } else if started || installer.isRunning || installer.done {
            // `started` covers the brief tick between pressing Install and the
            // runner flipping isRunning true, so the welcome doesn't flash back.
            installProgressView
        } else if essentialsInstalled {
            allSetView
        } else {
            welcomeView
        }
    }

    // MARK: - Header

    private var header: some View {
        VStack(spacing: 8) {
            MacNCheeseLogo(size: 84)
            Text(L("Welcome to MacNCheese"))
                .font(.title2).fontWeight(.bold)
            Text(L("Play Windows games on your Mac."))
                .font(.callout)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 24)
        .padding(.bottom, 14)
    }

    // MARK: - Loading

    private var loadingView: some View {
        VStack(spacing: 0) {
            header
            Spacer()
            ProgressView()
                .controlSize(.large)
            Text(L("Checking what's already installed…"))
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.top, 12)
            Spacer()
        }
    }

    // MARK: - Welcome (needs install)

    private var welcomeView: some View {
        VStack(spacing: 0) {
            header

            VStack(alignment: .leading, spacing: 14) {
                Text(L("MacNCheese will set up everything it needs to run games:"))
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                OnboardingFeatureRow(icon: "wineglass.fill", tint: .purple,
                                     title: L("Wine"),
                                     detail: L("Runs Windows apps and games."),
                                     installed: status?.hasWineStable ?? false)
                OnboardingFeatureRow(icon: "bolt.fill", tint: .orange,
                                     title: L("Wine D3DMetal"),
                                     detail: L("Apple's high-performance Direct3D engine."),
                                     installed: status?.hasWineD3DMetal ?? false)
                OnboardingFeatureRow(icon: "cpu.fill", tint: .blue,
                                     title: L("Graphics (DXVK + Mesa)"),
                                     detail: L("DirectX-to-Vulkan translation for 3D games."),
                                     installed: (status?.hasDxvk64 ?? false) && (status?.hasMesa ?? false))
                OnboardingFeatureRow(icon: "hammer.fill", tint: .gray,
                                     title: L("Tools"),
                                     detail: L("git, 7-Zip and wget used during setup."),
                                     installed: status?.hasTools ?? false)

                Toggle(isOn: $installEverything) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(L("Also install advanced graphics"))
                        Text(L("Wine Staging, DXMT and VKD3D-Proton. Larger download."))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(.top, 4)

                Text(L("This downloads a few hundred MB the first time and may take several minutes."))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.horizontal, 28)

            Spacer(minLength: 12)

            VStack(spacing: 8) {
                Button {
                    started = true
                    Task { await installer.run(actions: plannedActions(), backend: backend) }
                } label: {
                    Text(L("Install & Get Started"))
                        .fontWeight(.semibold)
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)

                Button(L("Skip for now")) { finish() }
                    .buttonStyle(.plain)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 28)
            .padding(.bottom, 22)
        }
    }

    // MARK: - Already set up

    private var allSetView: some View {
        VStack(spacing: 0) {
            header
            Spacer()
            Image(systemName: "checkmark.seal.fill")
                .font(.system(size: 54))
                .foregroundStyle(.green)
            Text(L("You're all set!"))
                .font(.title3).fontWeight(.semibold)
                .padding(.top, 10)
            Text(L("Wine and graphics support are already installed."))
                .font(.callout)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
                .padding(.top, 2)
            Spacer()
            Button {
                inSteamStep = true
            } label: {
                Text(L("Continue")).fontWeight(.semibold).frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
            .padding(.horizontal, 28)
            .padding(.bottom, 22)
        }
    }

    // MARK: - Install progress

    private var installProgressView: some View {
        VStack(spacing: 0) {
            header

            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    if installer.isRunning {
                        ProgressView().controlSize(.small)
                        Text(installer.currentAction.isEmpty ? L("Starting…") : installer.currentAction)
                            .font(.subheadline).fontWeight(.medium)
                    } else if installer.failed {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        Text(L("Setup finished with some errors"))
                            .font(.subheadline).fontWeight(.medium)
                    } else {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                        Text(L("Setup complete!"))
                            .font(.subheadline).fontWeight(.medium)
                    }
                    Spacer()
                }

                ScrollViewReader { proxy in
                    ScrollView {
                        Text(installer.logLines.joined(separator: "\n"))
                            .font(.system(.caption2, design: .monospaced))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                            .id("logBottom")
                    }
                    .frame(maxHeight: .infinity)
                    .background(.black.opacity(0.25))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .onChange(of: installer.logLines) {
                        proxy.scrollTo("logBottom", anchor: .bottom)
                    }
                }

                if installer.failed {
                    Text(L("You can finish anyway and retry missing pieces later in Settings → Setup."))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.horizontal, 28)

            Button {
                inSteamStep = true
            } label: {
                Text(installer.done ? L("Continue") : L("Installing…"))
                    .fontWeight(.semibold)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .keyboardShortcut(.defaultAction)
            .disabled(!installer.done)
            .padding(.horizontal, 28)
            .padding(.top, 14)
            .padding(.bottom, 22)
        }
    }

    // MARK: - Steam guide

    private var steamView: some View {
        VStack(spacing: 0) {
            // Steam-branded header (the Steam logo, not the MacNCheese one).
            VStack(spacing: 8) {
                SteamLogo(size: 84)
                Text(steamStarted ? L("Steam is installing") : L("Install Steam"))
                    .font(.title2).fontWeight(.bold)
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 28)
            .padding(.bottom, 14)

            if steamStarted {
                Spacer()
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 48)).foregroundStyle(.green)
                Text(L("Follow the Steam Setup window to finish, then come back here. A \"Steam\" bottle is ready in your library."))
                    .font(.callout).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 36).padding(.top, 12)
                Spacer()
                primaryButton(L("Finish")) { finish() }
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    Text(L("Pick your SteamSetup.exe — MacNCheese will create a ready-to-play Steam bottle and run the installer."))
                        .font(.callout).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(L("Don't have it? Download SteamSetup.exe from store.steampowered.com/about, then choose it here."))
                        .font(.caption2).foregroundStyle(.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.horizontal, 28)

                Spacer(minLength: 12)

                VStack(spacing: 8) {
                    if steamBusy {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text(L("Setting up Steam…")).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    primaryButton(L("Choose SteamSetup.exe…"), enabled: !steamBusy) { chooseSteamSetup() }
                    Button(L("Skip for now")) { finish() }
                        .buttonStyle(.plain).font(.callout).foregroundStyle(.secondary)
                        .disabled(steamBusy)
                }
                .padding(.horizontal, 28)
                .padding(.bottom, 22)
            }
        }
    }

    private func primaryButton(_ title: String, enabled: Bool = true, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).fontWeight(.semibold).frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .keyboardShortcut(.defaultAction)
        .disabled(!enabled)
        .padding(.horizontal, 28)
        .padding(.bottom, 22)
    }

    private func chooseSteamSetup() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.exe]
        panel.canChooseFiles = true
        panel.title = L("Select SteamSetup.exe")
        panel.nameFieldStringValue = "SteamSetup.exe"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        steamBusy = true
        Task {
            // Creates the "Steam" bottle, runs wineboot, then launches the chosen
            // SteamSetup.exe in it (backend background thread).
            await backend.createBottle(
                name: "Steam", launcherType: "steam", steamSetupPath: url.path)
            steamBusy = false
            steamStarted = true
        }
    }

    // MARK: - Logic

    private func loadStatus() {
        loadingStatus = true
        Task {
            // Retry on a transient nil so we don't conflate "backend not ready"
            // with "nothing installed" (which would misreport the badges and
            // queue redundant installs). Keep the spinner up while retrying.
            for _ in 0..<5 {
                if let s = await backend.getComponentsStatus() {
                    status = s
                    loadingStatus = false
                    return
                }
                try? await Task.sleep(nanoseconds: 600_000_000)
            }
            // Still nothing after retries — stop spinning and let the welcome
            // screen offer install (installer actions are idempotent).
            loadingStatus = false
        }
    }

    /// installer.sh actions to run, skipping anything already present so re-runs
    /// (or partial installs) only fetch what's missing.
    private func plannedActions() -> [String] {
        var actions: [String] = []
        func add(_ installed: Bool, _ action: String) { if !installed { actions.append(action) } }
        let s = status
        add(s?.hasTools ?? false, "install_tools")
        add(s?.hasWineStable ?? false, "install_wine")
        add(s?.hasWineD3DMetal ?? false, "install_wine_d3dmetal")
        if installEverything {
            add(s?.hasWineStaging ?? false, "install_wine_staging")
        }
        add(s?.hasDxvk64 ?? false, "install_dxvk")
        if installEverything {
            add(s?.hasVkd3d ?? false, "install_vkd3d")
            add(s?.hasDxmt ?? false, "install_dxmt")
        }
        add(s?.hasMesa ?? false, "install_mesa")
        return actions
    }

    private func finish() {
        onboardingComplete = true
        MacNCheeseSupport.markInitialized()
        Task { _ = await backend.getComponentsStatus() }
        dismiss()
    }
}

/// Load a PNG shipped in the app bundle (dmgndappbuilder.sh copies icon.png,
/// Steam.png, … into Resources), with dev fallbacks next to the binary and in
/// the source repo so `swift run` still finds them.
func bundledImage(_ name: String) -> NSImage? {
    if let url = Bundle.main.url(forResource: name, withExtension: "png") {
        return NSImage(contentsOf: url)
    }
    let binaryDir = URL(fileURLWithPath: CommandLine.arguments[0])
        .deletingLastPathComponent()
    for candidate in [
        binaryDir.appendingPathComponent("\(name).png"),
        URL(fileURLWithPath: NSHomeDirectory() + "/macndcheese/\(name).png"),
    ] {
        if let img = NSImage(contentsOf: candidate) { return img }
    }
    return nil
}

/// The MacNCheese app logo — icon.png (a transparent wine-glass mark) loaded
/// from the app bundle, the same image dmgndappbuilder.sh ships into Resources.
/// Falls back to a gamecontroller glyph on a gradient disc when the file isn't
/// bundled (e.g. running from `swift run`).
struct MacNCheeseLogo: View {
    var size: CGFloat = 96

    var body: some View {
        if let img = bundledImage("icon") {
            Image(nsImage: img)
                .resizable()
                .interpolation(.high)
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
        } else {
            ZStack {
                Circle()
                    .fill(
                        LinearGradient(
                            colors: [Color.yellow.opacity(0.9), Color.orange],
                            startPoint: .topLeading, endPoint: .bottomTrailing
                        )
                    )
                    .frame(width: size * 0.875, height: size * 0.875)
                    .shadow(color: .orange.opacity(0.35), radius: 12, y: 4)
                Image(systemName: "gamecontroller.fill")
                    .font(.system(size: size * 0.4, weight: .semibold))
                    .foregroundStyle(.white)
            }
        }
    }
}

/// The Steam logo — Steam.png (a white mark) on Steam's dark badge, loaded from
/// the app bundle. Used as the Steam onboarding step's hero. Falls back to a cart
/// glyph if the image isn't bundled. (Steam.png is white-on-transparent, so it
/// needs the dark backing to be visible.)
struct SteamLogo: View {
    var size: CGFloat = 84

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.225, style: .continuous)
                .fill(
                    LinearGradient(
                        colors: [Color(red: 0.10, green: 0.16, blue: 0.24),
                                 Color(red: 0.05, green: 0.07, blue: 0.10)],
                        startPoint: .top, endPoint: .bottom
                    )
                )
                .frame(width: size, height: size)
                .shadow(color: .black.opacity(0.3), radius: 10, y: 4)

            if let img = bundledImage("Steam") {
                Image(nsImage: img)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
                    .frame(width: size * 0.62, height: size * 0.62)
            } else {
                Image(systemName: "cart.fill")
                    .font(.system(size: size * 0.4, weight: .semibold))
                    .foregroundStyle(.white)
            }
        }
    }
}

/// One "what will be installed" row on the welcome screen, with a live
/// installed/queued badge.
private struct OnboardingFeatureRow: View {
    let icon: String
    let tint: Color
    let title: String
    let detail: String
    let installed: Bool

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 18))
                .foregroundStyle(tint)
                .frame(width: 30, height: 30)
                .background(tint.opacity(0.15), in: RoundedRectangle(cornerRadius: 7))

            VStack(alignment: .leading, spacing: 1) {
                Text(title).fontWeight(.medium)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if installed {
                Label(L("Installed"), systemImage: "checkmark.circle.fill")
                    .labelStyle(.iconOnly)
                    .foregroundStyle(.green)
            }
        }
    }
}
