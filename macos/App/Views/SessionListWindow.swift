import SwiftUI

struct SessionListWindow: View {
    @EnvironmentObject var store: SessionStore
    @State private var query = ""
    @State private var selection: Session.ID?
    @State private var sortOrder = [KeyPathComparator(\Session.lastTs, order: .reverse)]

    private var filtered: [Session] {
        let base = query.isEmpty ? store.sessions : store.sessions.filter {
            $0.project.localizedCaseInsensitiveContains(query) ||
            $0.summary.localizedCaseInsensitiveContains(query) ||
            $0.sessionId.contains(query)
        }
        return base.sorted(using: sortOrder)
    }

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                HStack {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField("검색 (프로젝트 / 메시지 / id)", text: $query)
                        .textFieldStyle(.plain)
                    Button { Task { await store.refreshOnce() } } label: {
                        Image(systemName: "arrow.clockwise")
                    }.buttonStyle(.borderless)
                }.padding(8)
                Divider()
                Table(filtered, selection: $selection, sortOrder: $sortOrder) {
                    TableColumn("") { s in Text(s.glyph) }.width(24)
                    TableColumn("프로젝트", value: \.project) { s in Text(s.project) }
                    TableColumn("메시지", value: \.summary) { s in
                        Text(s.summary).lineLimit(1).foregroundStyle(.secondary)
                    }
                    TableColumn("#", value: \.messages) { s in Text("\(s.messages)") }
                        .width(44)
                    TableColumn("최근", value: \.lastTs) { s in
                        Text(s.lastActivity.map(Self.shortDate) ?? "?")
                    }.width(120)
                }
            }
            .frame(minWidth: 520)

            SessionDetailView(sessionId: selection)
                .frame(minWidth: 320)
        }
    }

    static func shortDate(_ iso: String) -> String {
        guard let d = ISO8601DateFormatter().date(from: iso) else { return iso }
        let f = DateFormatter(); f.dateFormat = "MM-dd HH:mm"
        return f.string(from: d)
    }
}
