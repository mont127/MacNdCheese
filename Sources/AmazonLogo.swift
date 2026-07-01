import AppKit
import SwiftUI

/// Renders the Amazon Games SVG logo loaded from the app bundle.
/// Falls back to the `a.square.fill` SF Symbol if the file is unavailable
/// (no Amazon.svg asset has been sourced yet).
struct AmazonLogo: View {
    var size: CGFloat = 80
    var showBackground: Bool = true

    private var image: NSImage? {
        if let url = Bundle.main.url(forResource: "Amazon", withExtension: "svg") {
            return NSImage(contentsOf: url)
        }
        // Running from `swift run` / source — look next to the binary
        let binaryDir = URL(fileURLWithPath: CommandLine.arguments[0])
            .deletingLastPathComponent()
        let candidate = binaryDir.appendingPathComponent("Amazon.svg")
        return NSImage(contentsOf: candidate)
    }

    var body: some View {
        if let img = image {
            Image(nsImage: img)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
        } else {
            Image(systemName: "a.square.fill")
                .font(.system(size: size))
                .foregroundStyle(.orange.opacity(0.85))
        }
    }
}

/// A compact square icon for use in the sidebar and picker labels.
struct AmazonIcon: View {
    var size: CGFloat = 22

    private var image: NSImage? {
        if let url = Bundle.main.url(forResource: "Amazon", withExtension: "svg") {
            return NSImage(contentsOf: url)
        }
        let binaryDir = URL(fileURLWithPath: CommandLine.arguments[0])
            .deletingLastPathComponent()
        return NSImage(contentsOf: binaryDir.appendingPathComponent("Amazon.svg"))
    }

    var body: some View {
        if let img = image {
            Image(nsImage: img)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: size, height: size)
                .clipShape(RoundedRectangle(cornerRadius: size * 0.22))
        } else {
            Image(systemName: "a.square.fill")
                .foregroundStyle(.orange)
        }
    }
}
