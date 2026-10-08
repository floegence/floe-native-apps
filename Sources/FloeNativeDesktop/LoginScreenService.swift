import Foundation

public enum LoginScreenServiceState: String, Sendable {
    case unsupported, notInstalled = "not_installed", authorizationRequired = "authorization_required"
    case installing, active, stopped, failed, uninstalling
}

public struct LoginScreenServiceStatus: Sendable, Equatable {
    public let state: LoginScreenServiceState
    public let reason: String?
    public init(state: LoginScreenServiceState, reason: String? = nil) {
        self.state = state
        self.reason = reason
    }
}

public enum LoginScreenServiceError: Error, Equatable {
    case authorizationRequired
    case unsupported
    case unavailable
}

/// Explicit lifecycle boundary for the privileged login-screen helper. The
/// host application supplies an administrator-authorized installer; this type
/// never prompts, stores credentials, or installs during process startup.
public actor LoginScreenService {
    public typealias Installer = @Sendable () async throws -> Void
    private var status: LoginScreenServiceStatus
    private let install: Installer?
    private let uninstall: Installer?

    public init(supported: Bool = true, install: Installer? = nil, uninstall: Installer? = nil) {
        self.status = LoginScreenServiceStatus(state: supported ? .notInstalled : .unsupported)
        self.install = install
        self.uninstall = uninstall
    }

    public func currentStatus() -> LoginScreenServiceStatus { status }

    public func installService() async -> Result<LoginScreenServiceStatus, LoginScreenServiceError> {
        guard status.state != .unsupported else { return .failure(.unsupported) }
        guard status.state != .active else { return .success(status) }
        status = LoginScreenServiceStatus(state: .installing)
        guard let install else {
            status = LoginScreenServiceStatus(state: .authorizationRequired)
            return .failure(.authorizationRequired)
        }
        do {
            try await install()
            status = LoginScreenServiceStatus(state: .active)
            return .success(status)
        } catch {
            status = LoginScreenServiceStatus(state: .failed, reason: "install_failed")
            return .failure(.unavailable)
        }
    }

    public func uninstallService() async -> Result<LoginScreenServiceStatus, LoginScreenServiceError> {
        guard status.state != .unsupported else { return .failure(.unsupported) }
        guard status.state != .notInstalled else { return .success(status) }
        status = LoginScreenServiceStatus(state: .uninstalling)
        do {
            try await uninstall?()
            status = LoginScreenServiceStatus(state: .notInstalled)
            return .success(status)
        } catch {
            status = LoginScreenServiceStatus(state: .failed, reason: "uninstall_failed")
            return .failure(.unavailable)
        }
    }
}
