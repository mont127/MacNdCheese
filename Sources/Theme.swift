import SwiftUI

/// MacNCheese brand palette — cheese gold (primary) + wine (accent), matching the
/// "macndcheese UI" Claude Design system. `Color.brand` is the single app-wide
/// accent used for selection, primary actions and highlights. This is a
/// style-only layer: it recolors existing controls without changing any layout.
extension Color {
    static let cheese     = Color(red: 1.00, green: 0.761, blue: 0.294) // #FFC24B
    static let cheeseDeep = Color(red: 0.957, green: 0.655, blue: 0.165) // #F4A72A
    static let wine       = Color(red: 0.878, green: 0.278, blue: 0.431) // #E0476E
    static let wineDeep   = Color(red: 0.702, green: 0.184, blue: 0.333) // #B32F55
    /// Dark ink for text/glyphs that sit on top of the cheese gold.
    static let onCheese   = Color(red: 0.14, green: 0.10, blue: 0.02)

    /// App-wide accent (selection, primary buttons, accent glyphs).
    static let brand = cheese
}

extension LinearGradient {
    /// Cheese-gold gradient for prominent surfaces (e.g. the tile play button).
    static let cheese = LinearGradient(
        colors: [Color.cheese, Color.cheeseDeep],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
}
