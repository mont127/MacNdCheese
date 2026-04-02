import SwiftUI

struct CreateBottleSheet: View {
    @EnvironmentObject var backend: BackendClient
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var isCreating = false

    private var bottlePath: String {
        let base = NSHomeDirectory() + "/Games/MacNCheese"
        let safeName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return safeName.isEmpty ? base : base + "/\(safeName)"
    }

    var body: some View {
        VStack(spacing: 20) {
            Text("Create a Bottle")
                .font(.title2)
                .fontWeight(.bold)

            VStack(alignment: .leading, spacing: 6) {
                Text("Bottle Name")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                TextField("e.g. My Games", text: $name)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("Prefix Path")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(bottlePath)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Spacer()

            HStack {
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)

                Spacer()

                Button("Create") {
                    guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
                    isCreating = true
                    Task {
                        await backend.createBottle(name: name.trimmingCharacters(in: .whitespacesAndNewlines))
                        isCreating = false
                        dismiss()
                    }
                }
                .keyboardShortcut(.defaultAction)
                .buttonStyle(.borderedProminent)
                .tint(.cyan)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || isCreating)
            }
        }
        .padding(24)
        .frame(width: 420, height: 260)
        .background(.ultraThinMaterial)
    }
}
