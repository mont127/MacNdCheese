import AppKit
import SwiftUI

/// Renders the Epic Games SVG logo loaded from the app bundle.
/// Falls back to the `e.circle.fill` SF Symbol if the file is unavailable.
struct EpicLogo: View {
    var size: CGFloat = 80
    /// When true, show the logo on a transparent background (the SVG already
    /// includes its own dark rounded-rectangle background).
    var showBackground: Bool = true

    private var image: NSImage? {
        if let url = Bundle.main.url(forResource: "Epic", withExtension: "svg") {
            return NSImage(contentsOf: url)
        }
        // Running from `swift run` / source — look next to the binary
        let binaryDir = URL(fileURLWithPath: CommandLine.arguments[0])
            .deletingLastPathComponent()
        let candidate = binaryDir.appendingPathComponent("Epic.svg")
        return NSImage(contentsOf: candidate)
    }

    var body: some View {
        if let img = image {
            Image(nsImage: img)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size * (750.977 / 647.167)) // native aspect
        } else {
            Image(systemName: "e.circle.fill")
                .font(.system(size: size))
                .foregroundStyle(.indigo.opacity(0.85))
        }
    }
}

/// A compact square icon for use in the sidebar and picker labels.
struct EpicIcon: View {
    var size: CGFloat = 22

    private var image: NSImage? {
        if let url = Bundle.main.url(forResource: "Epic", withExtension: "svg") {
            return NSImage(contentsOf: url)
        }
        let binaryDir = URL(fileURLWithPath: CommandLine.arguments[0])
            .deletingLastPathComponent()
        return NSImage(contentsOf: binaryDir.appendingPathComponent("Epic.svg"))
    }

    var body: some View {
        if let img = image {
            Image(nsImage: img)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
                .clipShape(RoundedRectangle(cornerRadius: size * 0.22))
        } else {
            Image(systemName: "e.circle.fill")
                .foregroundStyle(.indigo)
        }
    }
}
