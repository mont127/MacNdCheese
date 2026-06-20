import SwiftUI

/// Top banner shown when a newer MacNdCheese release exists. Drives the in-app
/// updater: download the newest DMG -> extract the .app -> codesign -> swap the
/// running app -> relaunch (handled by BackendClient.applyAppUpdate + the
/// detached backend swapper; this view just shows state and triggers it).
struct AppUpdateBanner: View {
    @EnvironmentObject var backend: BackendClient
    @EnvironmentObject var updateChecker: UpdateChecker
    @State private var dismissed = false

    var body: some View {
        if updateChecker.updateAvailable && !dismissed {
            HStack(spacing: 12) {
                Image(systemName: updateChecker.installFailed
                      ? "exclamationmark.triangle.fill" : "arrow.down.circle.fill")
                    .foregroundStyle(updateChecker.installFailed ? .orange : Color.brand)

                VStack(alignment: .leading, spacing: 1) {
                    if updateChecker.installing {
                        Text(String(format: L("Updating to %@…"), updateChecker.latestVersion))
                            .font(.callout).fontWeight(.semibold)
                        Text(updateChecker.currentStep.isEmpty ? L("Working…") : updateChecker.currentStep)
                            .font(.caption2).foregroundStyle(.secondary)
                    } else if updateChecker.installFailed {
                        Text(L("Update failed"))
                            .font(.callout).fontWeight(.semibold)
                        Text(L("Use “Release notes” to update manually."))
                            .font(.caption2).foregroundStyle(.secondary)
                    } else {
                        Text(String(format: L("Update available: %@"), updateChecker.latestVersion))
                            .font(.callout).fontWeight(.semibold)
                        Text(String(format: L("You're on %@"), UpdateChecker.currentVersion))
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }

                Spacer()

                if updateChecker.installing {
                    ProgressView().controlSize(.small)
                } else {
                    if !updateChecker.dmgURL.isEmpty && !updateChecker.installFailed {
                        Button(L("Update & Restart")) {
                            updateChecker.install(backend: backend)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.small)
                    }
                    if let url = URL(string: updateChecker.releaseURL) {
                        Link(L("Release notes"), destination: url).font(.caption)
                    }
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
}
