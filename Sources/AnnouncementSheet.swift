import SwiftUI

/// Modal sheet showing the newest announcement from GitHub Discussions.
/// Displayed once per unique announcement; dismiss marks it as seen.
struct AnnouncementSheet: View {
    @ObservedObject var checker: AnnouncementChecker
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Header
            HStack(alignment: .top, spacing: 12) {
                if checker.isEmergency {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 28))
                        .foregroundStyle(.red)
                        .padding(.top, 2)
                } else {
                    Image(systemName: "megaphone.fill")
                        .font(.system(size: 28))
                        .foregroundStyle(.tint)
                        .padding(.top, 2)
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text(checker.isEmergency ? L("Emergency Announcement") : L("MacNCheese Announcement"))
                        .font(.system(size: 11, weight: .semibold))
                        .kerning(0.5)
                        .textCase(.uppercase)
                        .foregroundStyle(checker.isEmergency ? AnyShapeStyle(.red) : AnyShapeStyle(.secondary))
                    Text(checker.title)
                        .font(.title2)
                        .fontWeight(.semibold)
                        .fixedSize(horizontal: false, vertical: true)
                    if !checker.publishedDate.isEmpty {
                        Text(String(format: L("Posted %@"), prettyDate(checker.publishedDate)))
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                    }
                }

                Spacer()
            }
            .padding(20)

            Divider()

            // Body
            ScrollView {
                Text(checker.plainTextContent)
                    .font(.system(size: 13))
                    .lineSpacing(3)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(20)
            }
            .frame(maxHeight: 360)

            Divider()

            // Footer
            HStack {
                if !checker.url.isEmpty, let url = URL(string: checker.url) {
                    Link(destination: url) {
                        Label(L("Read on GitHub"), systemImage: "arrow.up.right.square")
                            .font(.system(size: 13))
                    }
                }
                Spacer()
                if checker.isEmergency {
                    // shown again at the next launch for as long as the post is an emergency
                    Button(L("Got it")) {
                        checker.dismissForNow()
                        dismiss()
                    }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
                } else {
                Button(L("Don't show again")) {
                    checker.markShown(id: checker.entryID)
                    dismiss()
                }
                .keyboardShortcut(.cancelAction)
                Button(L("Got it")) {
                    checker.markShown(id: checker.entryID)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
                }
            }
            .padding(20)
        }
        .frame(width: 560)
        .fixedSize(horizontal: false, vertical: true)
    }

    /// "2026-05-23T09:41:18+00:00" → "May 23, 2026"
    private func prettyDate(_ iso: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var date = formatter.date(from: iso)
        if date == nil {
            formatter.formatOptions = [.withInternetDateTime]
            date = formatter.date(from: iso)
        }
        guard let date else { return iso }
        let out = DateFormatter()
        out.dateStyle = .medium
        out.timeStyle = .none
        return out.string(from: date)
    }
}
