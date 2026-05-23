import SwiftUI

struct AnnouncementSheet: View {
    @Environment(\.dismiss) private var dismiss
    let checker: AnnouncementChecker

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(checker.title.isEmpty ? "Announcement" : checker.title)
                .font(.title2)
                .fontWeight(.bold)
                .frame(maxWidth: .infinity, alignment: .leading)

            if !checker.publishedDate.isEmpty {
                Text(checker.publishedDate)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Divider()

            ScrollView {
                Text(checker.plainTextContent.isEmpty ? checker.title : checker.plainTextContent)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }

            HStack {
                if !checker.url.isEmpty, let url = URL(string: checker.url) {
                    Button("Open in Browser") {
                        NSWorkspace.shared.open(url)
                    }
                    .buttonStyle(.bordered)
                }
                Spacer()
                Button("Done") {
                    checker.markShown(id: checker.currentId)
                    dismiss()
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(minWidth: 420, minHeight: 300)
        .background(Color(.windowBackgroundColor))
    }
}
