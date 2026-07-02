import SwiftUI
struct SessionDetailView: View {
    let sessionId: Session.ID?
    var body: some View { Text(sessionId ?? "세션 선택").padding() }
}
