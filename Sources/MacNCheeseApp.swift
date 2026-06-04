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

    /// Primary entry point for Spotlight taps on macOS. This is called before
    /// SwiftUI view modifiers on cold launch, so store the launch and also post
    /// a notification for already-running windows.
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
        AppDelegate.pendingLaunch = (bottlePath, appid)
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
    @StateObject private var updateChecker = UpdateChecker()
    @StateObject private var loc = LocalizationManager.shared
    @State private var urlHandler: MacNCheeseURLHandler?

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

                    let handler = MacNCheeseURLHandler(backend: backend)
                    urlHandler = handler

                    // Tell Siri/Shortcuts about this app's available actions.
                    // Required for shortcuts to appear in Shortcuts.app and be
                    // invocable via Siri.
                    MacNCheeseShortcuts.updateAppShortcutParameters()

                    // One-time migration: wipe the entire Spotlight index built
                    // by older builds that did not deduplicate Epic games.
                    let wipeKey = "SpotlightIndexer.wiped.v2"
                    if !UserDefaults.standard.bool(forKey: wipeKey) {
                        SpotlightIndexer.deleteAll()
                        UserDefaults.standard.set(true, forKey: wipeKey)
                    }

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
                Button(L("New Bottle…")) {
                    NotificationCenter.default.post(name: .createNewBottle, object: nil)
                }
                .keyboardShortcut("n", modifiers: .command)
            }
        }

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
