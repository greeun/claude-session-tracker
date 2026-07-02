import SwiftUI

struct SessionDetailView: View {
    let sessionId: Session.ID?
    @EnvironmentObject var store: SessionStore
    @State private var preview = ""
    @State private var loading = false

    private var session: Session? {
        store.sessions.first { $0.sessionId == sessionId }
    }

    var body: some View {
        Group {
            if let s = session {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text(s.glyph).font(.title2)
                        VStack(alignment: .leading) {
                            Text(s.project).font(.headline)
                            Text(s.cwd).font(.caption).foregroundStyle(.secondary)
                                .lineLimit(1).truncationMode(.middle)
                        }
                        Spacer()
                    }
                    HStack {
                        Button {
                            store.resume(s.sessionId)
                        } label: { Label(s.job != nil ? "Attach" : "Resume",
                                         systemImage: "play.fill") }
                        Button {
                            store.toggleDone(s)
                        } label: { Label(s.isDone ? "Undone" : "Done",
                                         systemImage: s.isDone ? "arrow.uturn.left" : "checkmark") }
                    }
                    Divider()
                    if loading {
                        ProgressView().frame(maxWidth: .infinity)
                    } else {
                        ScrollView {
                            Text(preview.isEmpty ? "(미리보기 없음)" : preview)
                                .font(.system(.body, design: .monospaced))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .padding()
            } else {
                Text("세션을 선택하세요").foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .task(id: sessionId) {
            guard let id = sessionId else { preview = ""; return }
            loading = true
            preview = await store.preview(id)
            loading = false
        }
    }
}
