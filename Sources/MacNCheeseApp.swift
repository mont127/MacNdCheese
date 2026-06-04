import SwiftUI
import AppKit

class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }
}

@main
struct MacNCheeseApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var backend = BackendClient()
    @StateObject private var announcements = AnnouncementChecker()
    @StateObject private var updateChecker = UpdateChecker()
    @StateObject private var loc = LocalizationManager.shared

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(backend)
                .environmentObject(announcements)
                .environmentObject(updateChecker)
                .environmentObject(loc)
                .onAppear {
                    backend.start()
                    announcements.check()
                    updateChecker.check()
                }
                .onDisappear { backend.stop() }
        }
        .windowStyle(.automatic)
        .defaultSize(width: 1100, height: 760)

        // Settings scene — hosts SettingsSheet so the gear button's
        // openSettings() (and the ⌘, shortcut) actually opens it. Without this
        // scene that action is a silent no-op, which is why Settings never
        // appeared when clicked.
        Settings {
            SettingsSheet()
                .environmentObject(backend)
                .environmentObject(loc)
        }
    }
}