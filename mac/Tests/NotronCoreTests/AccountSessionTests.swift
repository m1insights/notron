import XCTest
@testable import NotronCore

final class AccountSessionTests: XCTestCase {
    func testPKCEUsesS256AndCallbackIsOneUse() throws {
        let flow = try AuthorizationAttempt(authorizationURL: URL(string:"https://issuer.test/authorize")!, clientID:"native", audience:"api", redirectURI:"com.m1labs.notron:/callback")
        let items = URLComponents(url:flow.url,resolvingAgainstBaseURL:false)!.queryItems!
        XCTAssertEqual(items.first(where:{$0.name=="code_challenge_method"})?.value,"S256")
        XCTAssertNotEqual(items.first(where:{$0.name=="code_challenge"})?.value,flow.verifier)
        let state = items.first(where:{$0.name=="state"})!.value!
        XCTAssertEqual(try flow.consume(URL(string:"com.m1labs.notron:/callback?code=ok&state=\(state)")!),"ok")
        XCTAssertThrowsError(try flow.consume(URL(string:"com.m1labs.notron:/callback?code=ok&state=\(state)")!))
    }
    func testWrongStateCallbackAndCancellationAreTerminal() throws {
        for callback in ["com.m1labs.notron:/callback?code=ok&state=wrong","com.m1labs.notron:/other?code=ok&state=wrong","https://evil.test/callback?code=ok&state=wrong"] {
            let flow=try AuthorizationAttempt(authorizationURL:URL(string:"https://issuer.test/authorize")!,clientID:"native",audience:"api",redirectURI:"com.m1labs.notron:/callback")
            XCTAssertThrowsError(try flow.consume(URL(string:callback)!))
            XCTAssertThrowsError(try flow.consume(URL(string:callback)!))
        }
    }
    @MainActor func testSignoutStopsProcessingAndClearsOnlySessionCredential() async throws {
        let vault=MemoryVault(); vault.values["managed-refresh"]=Data("refresh".utf8); vault.values["storage-key"]=Data("notes-key".utf8)
        var stopped=false
        let session=AccountSession(vault:vault,transport:FakeTransport(),stopManaged:{stopped=true})
        await session.signOut()
        XCTAssertTrue(stopped)
        XCTAssertNil(vault.values["managed-refresh"])
        XCTAssertNotNil(vault.values["storage-key"])
        XCTAssertFalse(session.isSignedIn)
        do { _ = try await session.accessToken(); XCTFail("signed out") } catch {}
    }
    @MainActor func testNonceMismatchDoesNotStoreRefresh() async throws {
        let vault=MemoryVault();let transport=FakeTransport();transport.nonce="wrong"
        let session=AccountSession(vault:vault,transport:transport,stopManaged:{})
        do { try await session.complete(code:"code",verifier:"verifier",nonce:"expected");XCTFail("nonce") } catch {}
        XCTAssertNil(vault.values["managed-refresh"])
        XCTAssertFalse(session.isSignedIn)
    }
    @MainActor func testRefreshRotatesKeychainAndStopsOnRevocation() async throws {
        let vault=MemoryVault();let transport=FakeTransport()
        let session=AccountSession(vault:vault,transport:transport,stopManaged:{})
        try await session.complete(code:"code",verifier:"verifier",nonce:"expected")
        let access=try await session.accessToken()
        XCTAssertEqual(access,"access")
        transport.rejected=true
        do { _=try await session.accessToken(forceRefresh:true);XCTFail("revoked") } catch {}
        XCTAssertFalse(session.isSignedIn)
        XCTAssertNil(vault.values["managed-refresh"])
    }
    @MainActor func testFailedCredentialDeletionCannotResumeManagedSession() async throws {
        let vault=MemoryVault();let transport=FakeTransport()
        let session=AccountSession(vault:vault,transport:transport,stopManaged:{})
        try await session.complete(code:"code",verifier:"verifier",nonce:"expected")
        vault.deleteFails=true
        await session.signOut()
        do {_=try await session.accessToken();XCTFail("must remain stopped")}catch{}
        XCTAssertTrue(session.credentialCleanupFailed)
    }
    @MainActor func testRefreshRotationAndAccountDeletion() async throws {
        let vault=MemoryVault();let transport=FakeTransport()
        let session=AccountSession(vault:vault,transport:transport,stopManaged:{})
        try await session.complete(code:"code",verifier:"verifier",nonce:"expected")
        let token=try await session.accessToken(forceRefresh:true)
        XCTAssertEqual(token,"new")
        XCTAssertEqual(vault.values["managed-refresh"],Data("rotated".utf8))
        try await session.requestDeletion()
        XCTAssertFalse(session.isSignedIn)
        XCTAssertNil(vault.values["managed-refresh"])
    }
    func testCancellationRejectsPreviouslyValidCallback() throws {
        let flow=try AuthorizationAttempt(authorizationURL:URL(string:"https://issuer.test/authorize")!,clientID:"native",audience:"api",redirectURI:"com.m1labs.notron:/callback")
        let state=URLComponents(url:flow.url,resolvingAgainstBaseURL:false)!.queryItems!.first(where:{$0.name=="state"})!.value!
        flow.cancel()
        XCTAssertThrowsError(try flow.consume(URL(string:"com.m1labs.notron:/callback?code=ok&state=\(state)")!))
    }

}
final class MemoryVault: SessionVault {
    var values:[String:Data]=[:]
    var deleteFails=false
    func get(_ name:String)throws->Data?{values[name]}
    func put(_ name:String,value:Data)throws{values[name]=value}
    func delete(_ name:String)throws{if deleteFails{throw SessionError.unavailable};values.removeValue(forKey:name)}
}
@MainActor final class FakeTransport: SessionTransport {
    var nonce="expected";var rejected=false
    func exchange(code:String,verifier:String)async throws->TokenSet{TokenSet(accessToken:"access",refreshToken:"refresh",idToken:"id",expiresIn:300)}
    func refresh(_ token:String)async throws->TokenSet{if rejected{throw SessionError.unavailable};return TokenSet(accessToken:"new",refreshToken:"rotated",idToken:nil,expiresIn:300)}
    func verify(access:String,idToken:String)async throws->String{nonce}
    func identify(access:String)async throws->DeviceIdentity{if rejected{throw SessionError.unavailable};return DeviceIdentity(accountID:"account",deviceID:"device")}
    func revoke(access:String,deviceID:String)async throws{}
    func deleteAccount(access:String)async throws{}
    func revokeRefresh(_ token:String)async throws{}
}
