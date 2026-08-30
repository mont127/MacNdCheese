import SwiftUI
import AppKit

/// Asks the user to point MacNdCheese at their own D3DMetal, once, on a fresh install.
///
/// D3DMetal is Apple's, it ships inside the Game Porting Toolkit, and we have no licence to
/// redistribute it -- so the app no longer carrys a copy. Everything else in the graphics pack
/// is ours and still ships; only Apple's four DLLs and the native runtime are missing, and the
/// user already has them if they have downloaded the toolkit.
///
/// This only ever appears when the pack realy has no D3DMetal in it. Someone upgrading from a
/// build that still bundled it keeps what they have and never sees this.
struct D3DMetalSetupBanner: View {
    @EnvironmentObject var backend: BackendClient

    @State private var checked = false
    @State private var needed = false
    @State private var installing = false
    @State private var failure: String?
    @State private var dismissed = false

    var body: some View {
        if needed && !dismissed {
            HStack(spacing: 12) {
                Image(systemName: failure == nil
                      ? "cube.transparent" : "exclamationmark.triangle.fill")
                    .foregroundStyle(failure == nil ? Color.brand : .orange)

                VStack(alignment: .leading, spacing: 1) {
                    Text(L("D3DMetal isn't set up"))
                        .font(.callout).fontWeight(.semibold)
                    if installing {
                        Text(L("Copying D3DMetal…"))
                            .font(.caption2).foregroundStyle(.secondary)
                    } else if let failure {
                        Text(failure)
                            .font(.caption2).foregroundStyle(.secondary)
                            .lineLimit(2)
                    } else {
                        Text(L("Apple ships D3DMetal in the Game Porting Toolkit and we can't include it. Point us at your copy and we'll install it."))
                            .font(.caption2).foregroundStyle(.secondary)
                            .lineLimit(2)
                    }
                }

                Spacer()

                if installing {
                    ProgressView().controlSize(.small)
                } else {
                    Button(L("Choose folder…")) { pick() }
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
            .task { await check() }
        } else {
            Color.clear.frame(height: 0).task { await check() }
        }
    }

    private func check() async {
        // The unified wine has to exist first, else the pack it installs into is not there
        // yet and asking would just fail. Setup runs before this matters.
        guard !checked else { return }
        checked = true
        if let s = await backend.d3dmetalStatus() {
            needed = s.unified && !s.installed
        }
    }

    private func pick() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = true            // a GPTK .dmg is fine too, we mount it
        panel.allowsMultipleSelection = false
        panel.title = L("Select your D3DMetal redist folder")
        panel.message = L("Pick the redist folder from the Game Porting Toolkit (the one containing lib/external and lib/wine).")
        guard panel.runModal() == .OK, let url = panel.url else { return }
        installing = true
        failure = nil
        Task {
            let err = await backend.installD3DMetal(path: url.path)
            await MainActor.run {
                installing = false
                if let err {
                    failure = err
                } else {
                    needed = false
                }
            }
        }
    }
}
