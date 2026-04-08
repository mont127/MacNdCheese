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

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(backend)
                .onAppear { backend.start() }
                .onDisappear { backend.stop() }
        }
        .windowStyle(.automatic)
        .defaultSize(width: 1100, height: 760)
    }
}
