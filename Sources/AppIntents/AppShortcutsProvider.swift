import AppIntents

struct MacNCheeseShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: LaunchGameIntent(),
            phrases: [
                "Launch \(\.$game) in \(.applicationName)",
                "Play \(\.$game) in \(.applicationName)",
                "Start \(\.$game) in \(.applicationName)",
                "Open \(\.$game) with \(.applicationName)",
            ],
            shortTitle: "Launch Game",
            systemImageName: "gamecontroller.fill"
        )
    }
}
