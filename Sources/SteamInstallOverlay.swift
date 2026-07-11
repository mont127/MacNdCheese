import SwiftUI

/// Full-window "Installing Steam…" loading screen shown while a silent SteamSetup install runs.
/// SteamSetup's own GUI wizard doesnt reliably surface a window under wine, so the install runs
/// silently (/S) -- this overlay is how the user knows it's actualy workin. `step` is a plain value
/// (NOT @EnvironmentObject) becuse overlay content doesnt reliably inherit environmentObjects on this
/// macOS SwiftUI (same trap that crashed WineUpdateOverlay).
struct SteamInstallOverlay: View {
    let step: String

    var body: some View {
        ZStack {
            Color.black.opacity(0.9).ignoresSafeArea()
            VStack(spacing: 16) {
                ProgressView().controlSize(.large).tint(.white)
                Text(L("Installing Steam…"))
                    .font(.title2).fontWeight(.semibold).foregroundStyle(.white)
                Text(step.isEmpty ? L("Working…") : step)
                    .font(.callout).foregroundStyle(.white.opacity(0.85))
                    .multilineTextAlignment(.center)
                Text(L("Steam installs in the background — there's no separate installer window. This only takes a moment."))
                    .font(.caption2).foregroundStyle(.white.opacity(0.5))
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 380)
            }
            .padding(36)
        }
        .transition(.opacity)
    }
}
