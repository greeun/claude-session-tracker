import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject var store: SessionStore
    @Environment(\.openWindow) private var openWindow

    private var active: [Session] {
        store.sessions.filter { $0.status == "working" || $0.status == "waiting" }
    }

    var body: some View {
        if let err = store.lastError {
            Text(err).foregroundStyle(.red)
            Divider()
        }
        if active.isEmpty {
            Text("활성 세션 없음").foregroundStyle(.secondary)
        } else {
            ForEach(active.prefix(8)) { s in
                Button("\(s.glyph)  \(s.project) — \(s.summary.prefix(40))") {
                    store.resume(s.sessionId)
                }
            }
        }
        Divider()
        Button("cst 윈도우 열기") { openWindow(id: "main") }
        Button("새로고침") { Task { await store.refreshOnce() } }
        Divider()
        SettingsLink { Text("설정…") }
        Button("종료") { NSApplication.shared.terminate(nil) }
    }
}
