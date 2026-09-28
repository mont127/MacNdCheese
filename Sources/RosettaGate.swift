import SwiftUI

/// Bradar Apple Silicon Macs come without Rosetta 2, and without it every wine launch dies with
/// "Bad CPU type in executable" -- the engine n every game are x86_64. so once per launch we ask
/// the backend, and if Rosetta is missing we install it ourselfs: no Settings trip, no Terminal.
/// installer.sh tries it as the user first and falls back to the macOS password dialog.
@MainActor
final class RosettaGate: ObservableObject {
    enum Phase: Equatable {
        case idle, installing, installed, failed
    }

    @Published var phase: Phase = .idle
    private let runner = InstallRunner()
    private var checkd = false

    /// Once per launch. A nil status means the backend is not up yet, so the next
    /// isConnected change trys again.
    func checkAndInstall(backend: BackendClient) async {
        guard !checkd, phase != .installing else { return }
        guard let status = await backend.getComponentsStatus() else { return }
        checkd = true
        if status.needsRosetta && !status.hasRosetta {
            await install(backend: backend)
        }
    }

    func install(backend: BackendClient) async {
        guard phase != .installing else { return }
        phase = .installing
        await runner.run(actions: ["install_rosetta"], backend: backend)
        // trust the re-check over the exit code, a softwareupdate hiccup can go either way
        let nowThere = await backend.getComponentsStatus()?.hasRosetta ?? false
        phase = nowThere ? .installed : .failed
        if nowThere {
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            if phase == .installed { phase = .idle }
        }
    }
}

/// Owns the gate and hangs its banner under the update banner. A modifier so ContentView's
/// body stays light to type-check, same as OnboardingPresenter.
struct RosettaGatePresenter: ViewModifier {
    @EnvironmentObject var backend: BackendClient
    @StateObject private var gate = RosettaGate()

    func body(content: Content) -> some View {
        content
            .safeAreaInset(edge: .top, spacing: 0) {
                RosettaBanner(gate: gate)
            }
            .onAppear { check() }
            .onChange(of: backend.isConnected) { connected in
                if connected { check() }
            }
    }

    private func check() {
        Task { await gate.checkAndInstall(backend: backend) }
    }
}

struct RosettaBanner: View {
    @EnvironmentObject var backend: BackendClient
    @ObservedObject var gate: RosettaGate
    @State private var dismissed = false

    var body: some View {
        if gate.phase != .idle && !dismissed {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .foregroundStyle(tint)

                VStack(alignment: .leading, spacing: 1) {
                    Text(title)
                        .font(.callout).fontWeight(.semibold)
                    Text(subtitle)
                        .font(.caption2).foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }

                Spacer()

                if gate.phase == .installing {
                    ProgressView().controlSize(.small)
                } else if gate.phase == .failed {
                    Button(L("Try Again")) {
                        Task { await gate.install(backend: backend) }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    Button {
                        dismissed = true
                    } label: {
                        Image(systemName: "xmark")
                    }
                    .buttonStyle(.borderless)
                    .help(L("Dismiss"))
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(.ultraThinMaterial)
            .overlay(Divider(), alignment: .bottom)
        }
    }

    private var icon: String {
        switch gate.phase {
        case .failed: return "exclamationmark.triangle.fill"
        case .installed: return "checkmark.circle.fill"
        default: return "cpu"
        }
    }

    private var tint: Color {
        switch gate.phase {
        case .failed: return .orange
        case .installed: return .green
        default: return Color.brand
        }
    }

    private var title: String {
        switch gate.phase {
        case .failed: return L("Rosetta 2 couldn't be installed")
        case .installed: return L("Rosetta 2 installed")
        default: return L("Installing Rosetta 2…")
        }
    }

    private var subtitle: String {
        switch gate.phase {
        case .failed:
            return L("Windows games need it. Check your connection, or run: softwareupdate --install-rosetta --agree-to-license")
        case .installed:
            return L("Windows games can run now.")
        default:
            return L("Windows games run through Rosetta. macOS may ask for your password.")
        }
    }
}
