import SwiftUI

/// Full-window "Installing EA App…" loading screen, mirroring SteamInstallOverlay --
/// the EA App installer runs silently (/S) so this is how the user knows it's actually
/// working. `step` is a plain value (NOT @EnvironmentObject), same reason as SteamInstallOverlay.
struct EAAppInstallOverlay: View {
    let step: String

    var body: some View {
        ZStack {
            Color.black.opacity(0.9).ignoresSafeArea()
            VStack(spacing: 16) {
                ProgressView().controlSize(.large).tint(.white)
                Text(L("Installing the EA App…"))
                    .font(.title2).fontWeight(.semibold).foregroundStyle(.white)
                Text(step.isEmpty ? L("Working…") : step)
                    .font(.callout).foregroundStyle(.white.opacity(0.85))
                    .multilineTextAlignment(.center)
                Text(L("This title is fulfilled through the EA App, not Epic directly. Once it's installed, you'll sign in there yourself to finish setting it up."))
                    .font(.caption2).foregroundStyle(.white.opacity(0.5))
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 380)
            }
            .padding(36)
        }
        .transition(.opacity)
    }
}
