import SwiftUI

/// Shown instead of the generic empty-state / "Launch Steam" prompt when the
/// active bottle's folder can't be found on disk — almost always because it
/// lives on an external drive that's since been unmounted. Replaces the
/// previously-misleading flow where the user would click "Launch Steam" and
/// it would silently fail because the drive backing the bottle wasn't there.
struct DriveDisconnectedView: View {
    @EnvironmentObject var backend: BackendClient
    let bottle: Bottle?

    /// Parses a friendly volume name out of "/Volumes/<Name>/...". Returns nil
    /// for paths not under /Volumes (e.g. an internal-disk folder that was
    /// moved or deleted), where a generic message is shown instead.
    private var volumeName: String? {
        guard let path = bottle?.path, path.hasPrefix("/Volumes/") else { return nil }
        let rest = path.dropFirst("/Volumes/".count)
        return rest.split(separator: "/").first.map(String.init)
    }

    var body: some View {
        VStack(spacing: 0) {
            Spacer()

            Image(systemName: "externaldrive.badge.exclamationmark")
                .font(.system(size: 72))
                .foregroundStyle(.orange)
                .padding(.bottom, 12)

            Text(L("Drive Not Connected"))
                .font(.title)
                .fontWeight(.bold)

            Group {
                if let vol = volumeName, let name = bottle?.name {
                    Text(String(format: L("\"%@\" is on the drive \"%@\", which isn't connected. Reconnect it to continue."), name, vol))
                } else {
                    Text(L("Can't find this bottle's folder. If it's on an external drive, reconnect it."))
                }
            }
            .font(.subheadline)
            .foregroundStyle(.secondary)
            .multilineTextAlignment(.center)
            .padding(.horizontal, 40)
            .padding(.top, 4)

            Spacer().frame(height: 28)

            Button {
                recheck()
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.clockwise")
                    Text(L("Try Again")).fontWeight(.bold)
                }
                .frame(minWidth: 140, minHeight: 44)
            }
            .buttonStyle(.borderedProminent)
            .tint(Color.brand)
            .controlSize(.large)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // Reconnecting the drive resolves this automatically — BackendClient
        // observes volume mounts globally and reloads the active bottle — so
        // "Try Again" below is a manual nudge for the rare case that doesn't
        // fire a mount notification (e.g. a network share becoming reachable).
    }

    private func recheck() {
        guard let path = bottle?.path else { return }
        // Re-runs the same reachability check selectBottle already does; if
        // the drive is back, this also kicks off the normal scan, and
        // ContentView reactively swaps this view out on its own.
        backend.selectBottle(path)
    }
}
