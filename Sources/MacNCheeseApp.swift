import SwiftUI
import AppKit

extension Notification.Name {
    static let createNewBottle = Notification.Name("MacNCheese.createNewBottle")
}

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
