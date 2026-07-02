import SwiftUI
struct SettingsView: View {
    @ObservedObject var config: AppConfig
    var body: some View { Text("설정").padding() }
}
