import Foundation
import UserNotifications

final class NotificationManager {
    func requestAuthorization() {
        UNUserNotificationCenter.current()
            .requestAuthorization(options: [.alert, .sound]) { _, _ in }
    }

    func post(_ specs: [NotifSpec]) {
        let center = UNUserNotificationCenter.current()
        for spec in specs {
            let content = UNMutableNotificationContent()
            content.title = spec.title
            content.body = spec.body
            content.userInfo = ["sessionId": spec.sessionId]
            let req = UNNotificationRequest(identifier: UUID().uuidString,
                                            content: content, trigger: nil)
            center.add(req)
        }
    }
}
