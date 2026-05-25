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
            systemImageName: "gamecontroller.fill",
            // parameterPresentation makes Siri expand the game parameter into
            // individual chips — one per installed game — so the user sees
            // "Launch Among Us", "Launch Portal", etc. as tappable suggestions
            // when typing or speaking anything related to launching a game.
            // This is the same mechanism used by "Ouvrir MacNdCheese Launcher"
            // chips: the system shows one chip per entity in the options collection.
            parameterPresentation: ParameterPresentation(
                for: \.$game,
                summary: Summary("Launch \(\.$game)"),
                optionsCollections: {
                    OptionsCollection(GameEntityQuery(), title: "Games", systemImageName: "gamecontroller.fill")
                }
            )
        )
    }
}
