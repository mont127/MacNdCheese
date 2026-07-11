import AppIntents
import CoreSpotlight
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

    func application(_ application: NSApplication, open urls: [URL]) {
        PendingOpen.shared.enqueue(urls)
        NotificationCenter.default.post(name: .openWindowsExecutable, object: nil)
    }

    // ── Quit-time Wine cleanup (field report: MiKo/Hafliss) ─────────────
    // Quitting the launcher used to leave Wine processes running invisibly in
    // the background. On quit, if MacNCheese's Wine is still alive, honor the
    // saved preference ("ask" | "kill" | "leave") or ask. Matching is on OUR
    // portable deps path only — other Wine installs (CrossOver/Whisky) are
    // never touched — and it works even if the backend already exited.
    private static let wineMatchPattern = "Application Support/MacNCheese/deps"

    /// Real executable path of a pid (libproc). Wine's Windows-side processes
    /// (services.exe, winedevice.exe, the game itself) show a pure "C:\..."
    /// argv in ps — invisible to command-line matching — but their true binary
    /// is our wine loader under deps. Verified live: 8/8 such pids resolved to
    /// the deps path. Other Wine installs resolve to THEIR paths, so they are
    /// never touched.
    private func pidExecutable(_ pid: pid_t) -> String {
        var buf = [CChar](repeating: 0, count: 4096)
        let n = proc_pidpath(pid, &buf, UInt32(buf.count))
        return n > 0 ? String(cString: buf) : ""
    }

    /// All host pids belonging to MacNCheese's Wine: unix-path matches (wine,
    /// wineserver, gstreamer helpers) + Windows-argv processes whose real
    /// executable lives under our deps dir.
    private func macNCheeseWinePIDs() -> [pid_t] {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/ps")
        p.arguments = ["-axo", "pid=,command="]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = Pipe()
        guard (try? p.run()) != nil else { return [] }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard let text = String(data: data, encoding: .utf8) else { return [] }

        let me = ProcessInfo.processInfo.processIdentifier
        var pids: [pid_t] = []
        for raw in text.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard let sp = line.firstIndex(of: " "), let pid = pid_t(line[..<sp]) else { continue }
            if pid == me { continue }
            let cmd = String(line[line.index(after: sp)...])
            if cmd.contains("backend_server.py") || cmd.contains(".app/Contents/MacOS/MacNCheese") { continue }
            if cmd.contains(Self.wineMatchPattern) {
                pids.append(pid)
            } else if cmd.count > 2, Array(cmd)[1] == ":", Array(cmd)[2] == "\\",
                      pidExecutable(pid).contains(Self.wineMatchPattern) {
                pids.append(pid)
            }
        }
        return pids
    }

    private func macNCheeseWineRunning() -> Bool {
        !macNCheeseWinePIDs().isEmpty
    }

    private func killAllMacNCheeseWine() {
        // Polite first, then definitive — hung games ignore SIGTERM.
        for sig in [SIGTERM, SIGKILL] {
            let pids = macNCheeseWinePIDs()
            if pids.isEmpty { break }
            for pid in pids { kill(pid, sig) }
            if sig == SIGTERM { usleep(900_000) }
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard macNCheeseWineRunning() else { return .terminateNow }

        switch UserDefaults.standard.string(forKey: "quit_wine_behavior") ?? "ask" {
        case "kill":
            killAllMacNCheeseWine()
            return .terminateNow
        case "leave":
            return .terminateNow
        default:
            let alert = NSAlert()
            alert.messageText = L("Wine is still running")
            alert.informativeText = L("Games or Wine processes started by MacNCheese are still running. Quit them too?")
            alert.addButton(withTitle: L("Quit Wine & Exit"))
            alert.addButton(withTitle: L("Leave Running & Exit"))
            alert.addButton(withTitle: L("Cancel"))
            alert.showsSuppressionButton = true
            alert.suppressionButton?.title = L("Remember my choice")
            let resp = alert.runModal()
            if resp == .alertThirdButtonReturn { return .terminateCancel }
            let kill = (resp == .alertFirstButtonReturn)
            if alert.suppressionButton?.state == .on {
                UserDefaults.standard.set(kill ? "kill" : "leave", forKey: "quit_wine_behavior")
            }
            if kill { killAllMacNCheeseWine() }
            return .terminateNow
        }
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
    @StateObject private var wineGate = WineVersionGate()
    @State private var urlHandler: MacNCheeseURLHandler?

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(backend)
                .environmentObject(announcements)
                .environmentObject(updateChecker)
                .environmentObject(loc)
                .environmentObject(wineGate)
                .overlay { WineUpdateOverlay() }
                .onAppear {
                    backend.start()
                    announcements.check()
                    updateChecker.check(autoInstallWith: backend)
                    // Launch-time wine version gate: if the on-disk wine is older than this
                    // app version, re-sync it (blockin overlay) then stamp the marker file.
                    wineGate.check(with: backend)

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
