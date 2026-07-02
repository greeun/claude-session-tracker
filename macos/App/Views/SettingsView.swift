import SwiftUI

struct SettingsView: View {
    @ObservedObject var config: AppConfig

    var body: some View {
        Form {
            Section("cst") {
                TextField("cst 실행 경로", text: $config.cstPath)
                Text("현재 해석: \(config.resolvedCstPath)")
                    .font(.caption).foregroundStyle(.secondary)
                Stepper("폴링 간격: \(config.pollInterval, specifier: "%.0f")s",
                        value: $config.pollInterval, in: 1...30, step: 1)
            }
            Section("알림") {
                Toggle("입력 대기(!)로 전환 시", isOn: $config.notifWaiting)
                Toggle("작업→유휴(◦) 전환 시", isOn: $config.notifIdle)
                Toggle("done(✓) 표시 시", isOn: $config.notifDone)
                Toggle("bg 실패/중지 시", isOn: $config.notifBgFailed)
            }
        }
        .formStyle(.grouped)
        .frame(width: 420)
        .padding()
    }
}
