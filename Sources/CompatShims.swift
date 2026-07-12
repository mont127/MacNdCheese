import SwiftUI
import AppKit

/// Cross-version shims for SwiftUI modifiers introduced after our macOS 12 floor.
/// Each one degrades to a no-op on older systems rather than gating whole features.
extension View {
    @ViewBuilder
    func hideScrollBackgroundCompat() -> some View {
        if #available(macOS 13, *) {
            self.scrollContentBackground(.hidden)
        } else {
            self
        }
    }

    @ViewBuilder
    func scrollClipDisabledCompat() -> some View {
        if #available(macOS 14, *) {
            self.scrollClipDisabled()
        } else {
            self
        }
    }

    @ViewBuilder
    func contentMarginsTopCompat(_ length: CGFloat) -> some View {
        if #available(macOS 14, *) {
            self.contentMargins(.top, length, for: .scrollContent)
        } else {
            self
        }
    }

    @ViewBuilder
    func numericContentTransitionCompat() -> some View {
        if #available(macOS 13, *) {
            self.contentTransition(.numericText())
        } else {
            self
        }
    }
}

/// Opens the Settings scene without the macOS 14-only `\.openSettings` environment
/// action. `showSettingsWindow:` is the selector SwiftUI itself wires up for the
/// `Settings {}` scene from macOS 13 onward; Ventura renamed it from `showPreferencesWindow:`.
func openAppSettingsCompat() {
    if #available(macOS 13, *) {
        NSApp.sendAction(Selector(("showSettingsWindow:")), to: nil, from: nil)
    } else {
        NSApp.sendAction(Selector(("showPreferencesWindow:")), to: nil, from: nil)
    }
}
