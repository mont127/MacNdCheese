import AppIntents

/// "Hey Siri, launch [game] in MacNCheese"
///
/// openAppWhenRun = true activates the app before perform() runs so the
/// notification is received by the live SwiftUI view hierarchy.
/// Siri phrase registration requires the app to be installed as a proper .app bundle
/// (run install.sh); it does not work when running the raw swift build binary directly.
struct LaunchGameIntent: AppIntent {
    static let title: LocalizedStringResource = "Launch Game"
    static let description = IntentDescription(
        "Launch a Windows game via MacNCheese",
        categoryName: "Gaming"
    )
    static let openAppWhenRun = true

    // parameterSummary is what Spotlight and Apple Intelligence use to understand
    // what the intent does in natural language. Without it, Spotlight won't invoke
    // the action and the LLM has no structured representation to reason against.
    // macOS 26 / WWDC25: Spotlight can now invoke app actions if parameterSummary
    // is present — enabling "⌘Space → Launch Among Us" without saying the app name.
    static var parameterSummary: some ParameterSummary {
        Summary("Launch \(\.$game)")
    }

    @Parameter(title: "Game", requestValueDialog: "Which game would you like to launch?")
    var game: GameEntity

    func perform() async throws -> some IntentResult {
        NotificationCenter.default.post(
            name: .launchGameFromIntent,
            object: nil,
            userInfo: ["bottlePath": game.bottlePath, "appid": game.appid]
        )
        return .result()
    }
}
