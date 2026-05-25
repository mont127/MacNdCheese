import AppIntents
import CoreSpotlight
import SwiftUI
import AppKit

class AppDelegate: NSObject, NSApplicationDelegate {
    /// Set when a Spotlight tap arrives before the SwiftUI view is ready.
    /// MacNCheeseApp checks this in onAppear and fires the launch once the
    /// backend is started.
    static var pendingLaunch: (bottlePath: String, appid: String)?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return true
    }

    /// Primary entry point for Spotlight taps on macOS — called before SwiftUI
    /// view modifiers, so it works for both cold and warm app launches.
    func application(
        _ application: NSApplication,
        continue userActivity: NSUserActivity,
        restorationHandler: @escaping ([NSUserActivityRestoring]) -> Void
    ) -> Bool {
        guard userActivity.activityType == CSSearchableItemActionType,
              let uid = userActivity.userInfo?[CSSearchableItemActivityIdentifier] as? String,
              let cached = GameIndexCache.game(byUID: uid) else { return false }

        let bottlePath = cached.bottlePath
        let appid = cached.appid

        // Store so onAppear can pick it up on cold launch.
        AppDelegate.pendingLaunch = (bottlePath, appid)

        // Also post a notification — if the view is already live, its .onReceive
        // handler will fire immediately and process the launch right away.
        NotificationCenter.default.post(
            name: .launchGameFromSpotlight,
            object: nil,
            userInfo: ["bottlePath": bottlePath, "appid": appid]
        )
        return true
    }
}

@main
struct MacNCheeseApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var backend = BackendClient()
    @StateObject private var announcements = AnnouncementChecker()
    @State private var urlHandler: MacNCheeseURLHandler?

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(backend)
                .environmentObject(announcements)
                .onAppear {
                    backend.start()
                    announcements.check()
                    let handler = MacNCheeseURLHandler(backend: backend)
                    urlHandler = handler

                    // Tell Siri/Shortcuts about this app's available actions.
                    // Required for shortcuts to appear in Shortcuts.app and be
                    // invocable via Siri — must be called on every app launch.
                    MacNCheeseShortcuts.updateAppShortcutParameters()

                    // One-time migration: wipe the entire Spotlight index built by
                    // older versions of the app that didn't deduplicate Epic games.
                    let wipeKey = "SpotlightIndexer.wiped.v2"
                    if !UserDefaults.standard.bool(forKey: wipeKey) {
                        SpotlightIndexer.deleteAll()
                        UserDefaults.standard.set(true, forKey: wipeKey)
                    }

                    // Process a Spotlight tap that arrived before the view was ready.
                    if let pending = AppDelegate.pendingLaunch {
                        AppDelegate.pendingLaunch = nil
                        handler.launch(bottlePath: pending.bottlePath, appid: pending.appid)
                    }
                }
                .onDisappear { backend.stop() }
                .onOpenURL { url in
                    urlHandler?.handle(url)
                }
                .onReceive(NotificationCenter.default.publisher(for: .launchGameFromSpotlight)) { notif in
                    guard let bottlePath = notif.userInfo?["bottlePath"] as? String,
                          let appid = notif.userInfo?["appid"] as? String else { return }
                    // Clear the pending flag — we're handling it here.
                    AppDelegate.pendingLaunch = nil
                    urlHandler?.launch(bottlePath: bottlePath, appid: appid)
                }
                .onReceive(NotificationCenter.default.publisher(for: .launchGameFromIntent)) { notif in
                    guard let bottlePath = notif.userInfo?["bottlePath"] as? String,
                          let appid = notif.userInfo?["appid"] as? String else { return }
                    urlHandler?.launch(bottlePath: bottlePath, appid: appid)
                }
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
