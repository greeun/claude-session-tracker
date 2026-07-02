import SwiftUI

@main
struct CstApp: App {
    var body: some Scene {
        MenuBarExtra("cst", systemImage: "circle.dashed") {
            Button("종료") { NSApplication.shared.terminate(nil) }
        }
    }
}
