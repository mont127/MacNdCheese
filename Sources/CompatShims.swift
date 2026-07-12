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
/// action.
///
/// SwiftUI auto-inserts a "Settings…" item into the app menu for any `Settings {}`
/// scene, but what that item is wired to has changed across macOS versions: the
/// `showSettingsWindow:` selector (broadcastable via the responder chain) on macOS
/// 13/14, but a closure-based `menuAction:` target (a private `SwiftUI.MenuItemCallback`
/// object) on newer releases — verified live on macOS 26, where no responder in the
/// chain implements `showSettingsWindow:` at all, so broadcasting it was a silent
/// no-op (the gear button visibly did nothing, while the real "Settings…" ⌘, item
/// worked). Rather than chase whichever selector today's macOS happens to use,
/// invoke that auto-generated item directly. It's the only app-menu entry that
/// isn't one of AppKit's fixed standard items, so it can be found without depending
/// on its title (which the system — not this app's L()  — localizes).
func openAppSettingsCompat() {
    let standardAppMenuActions: Set<String> = [
        "orderFrontStandardAboutPanel:", "submenuAction:", "hide:",
        "hideOtherApplications:", "unhideAllApplications:", "terminate:",
    ]
    guard let appMenuItems = NSApp.mainMenu?.items.first?.submenu?.items else { return }
    for item in appMenuItems {
        guard let action = item.action, !standardAppMenuActions.contains(NSStringFromSelector(action)) else { continue }
        NSApp.sendAction(action, to: item.target, from: item)
        return
    }
}
