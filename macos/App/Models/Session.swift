import Foundation

struct Session: Codable, Identifiable, Hashable {
    let sessionId: String
    let shortId: String?
    let cwd: String
    let project: String
    let status: String        // working | waiting | idle | ended | done
    let glyph: String
    let isLive: Bool
    let isDone: Bool
    let messages: Int
    let summary: String
    let lastActivity: String?
    let lastTs: Int
    let gitBranch: String
    let job: Job?
    let prs: [PR]
    let pinned: Bool

    var id: String { sessionId }

    struct Job: Codable, Hashable {
        let short: String?
        let template: String
        let branch: String
        let worktreePath: String
        let state: String?
        let tempo: String?
        let alive: Bool
    }

    struct PR: Codable, Hashable {
        let host: String
        let repo: String
        let number: Int
        let url: String
    }
}

struct SessionsPayload: Codable {
    let schema: Int
    let version: String
    let sessions: [Session]
    let counts: [String: Int]
}
