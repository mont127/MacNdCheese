import SwiftUI
import AppKit

extension Notification.Name {
    static let createNewBottle = Notification.Name("MacNCheese.createNewBottle")
    /// Posted when Finder hands the app one or more .exe/.msi files to open.
    static let openWindowsExecutable = Notification.Name("MacNCheese.openWindowsExecutable")
}

/// Holds executables handed to the app via Finder until ContentView is ready to
/// present the bottle picker. Needed because `application(_:open:)` can fire on a
/// cold launch before the SwiftUI view hierarchy (and BackendClient) exist.
final class PendingOpen {
    static let shared = PendingOpen()
    private(set) var urls: [URL] = []

    func enqueue(_ newURLs: [URL]) {
        let exts: Set<String> = ["exe", "msi"]
        urls.append(contentsOf: newURLs.filter { exts.contains($0.pathExtension.lowercased()) })
    }

    func drain() -> [URL] {
        let out = urls
        urls = []
        return out
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        PendingOpen.shared.enqueue(urls)
        NotificationCenter.default.post(name: .openWindowsExecutable, object: nil)
    }
}

@main
struct MacNCheeseApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var backend = BackendClient()
    @StateObject private var announcements = AnnouncementChecker()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(backend)
                .environmentObject(announcements)
                .onAppear {
                    backend.start()
                    announcements.check()
                }
                .onDisappear { backend.stop() }
        }
        .windowStyle(.automatic)
        .defaultSize(width: 1100, height: 760)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Bottle…") {
                    NotificationCenter.default.post(name: .createNewBottle, object: nil)
                }
                .keyboardShortcut("n", modifiers: .command)
            }
        }

        Settings {
            SettingsSheet()
                .environmentObject(backend)
        }
    }
}
