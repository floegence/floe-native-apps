import XCTest
@testable import FloeNativeDesktop

final class LoginScreenServiceTests: XCTestCase {
    func testInstallWithoutExplicitAdministratorAuthorizationDoesNotCreateActiveService() async {
        let service = LoginScreenService()
        let result = await service.installService()
        guard case .failure(.authorizationRequired) = result else { return XCTFail("expected authorization") }
        let status = await service.currentStatus()
        XCTAssertEqual(status.state, .authorizationRequired)
    }

    func testInstallAndUninstallAreExplicitAndReversible() async {
        let service = LoginScreenService(install: {}, uninstall: {})
        guard case .success(let active) = await service.installService() else { return XCTFail("install failed") }
        XCTAssertEqual(active.state, .active)
        guard case .success(let removed) = await service.uninstallService() else { return XCTFail("uninstall failed") }
        XCTAssertEqual(removed.state, .notInstalled)
    }
}
