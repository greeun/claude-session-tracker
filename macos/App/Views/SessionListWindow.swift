import SwiftUI
struct SessionListWindow: View {
    @EnvironmentObject var store: SessionStore
    var body: some View { Text("sessions: \(store.sessions.count)").padding() }
}
