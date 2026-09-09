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
        let session=AccountSession(vault:vault,transport:FakeTransport(),barrier:MemoryBarrier(),stopManaged:{stopped=true})
        await session.signOut()
        XCTAssertTrue(stopped)
        XCTAssertNil(vault.values["managed-refresh"])
        XCTAssertNotNil(vault.values["storage-key"])
        XCTAssertFalse(session.isSignedIn)
        do { _ = try await session.accessToken(); XCTFail("signed out") } catch {}
    }
    @MainActor func testNonceMismatchDoesNotStoreRefresh() async throws {
        let vault=MemoryVault();let transport=FakeTransport();transport.nonce="wrong"
        let session=AccountSession(vault:vault,transport:transport,barrier:MemoryBarrier(),stopManaged:{})
        do { try await session.complete(code:"code",verifier:"verifier",nonce:"expected");XCTFail("nonce") } catch {}
        XCTAssertNil(vault.values["managed-refresh"])
        XCTAssertFalse(session.isSignedIn)
    }
    @MainActor func testRefreshRotatesKeychainAndStopsOnRevocation() async throws {
        let vault=MemoryVault();let transport=FakeTransport()
        let session=AccountSession(vault:vault,transport:transport,barrier:MemoryBarrier(),stopManaged:{})
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
        let session=AccountSession(vault:vault,transport:transport,barrier:MemoryBarrier(),stopManaged:{})
        try await session.complete(code:"code",verifier:"verifier",nonce:"expected")
        vault.deleteFails=true
        await session.signOut()
        do {_=try await session.accessToken();XCTFail("must remain stopped")}catch{}
        XCTAssertTrue(session.credentialCleanupFailed)
    }
    @MainActor func testRefreshRotationAndAccountDeletion() async throws {
        let vault=MemoryVault();let transport=FakeTransport()
        let session=AccountSession(vault:vault,transport:transport,barrier:MemoryBarrier(),stopManaged:{})
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

    @MainActor func testRestartAfterFailedSignoutDoesNotReuseRefresh() async throws {
        let vault=MemoryVault();let transport=FakeTransport()
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer {try? FileManager.default.removeItem(at:directory)}
        let session=AccountSession(vault:vault,transport:transport,barrier:FileSessionBarrier(directory:directory),stopManaged:{})
        try await session.complete(code:"code",verifier:"verifier",nonce:"expected")
        vault.deleteFails=true;transport.revocationFails=true
        await session.signOut()
        let restarted=AccountSession(vault:vault,transport:transport,barrier:FileSessionBarrier(directory:directory),stopManaged:{})
        do {_=try await restarted.accessToken();XCTFail("restart must stay signed out")}catch{}
        XCTAssertTrue(restarted.credentialCleanupFailed)
        XCTAssertFalse(restarted.isSignedIn)
        XCTAssertEqual(transport.refreshCalls,0)
        XCTAssertEqual(try FileSessionBarrier(directory:directory).read(),.cleanupPending)
        // Retrying cleanup does not permit silent refresh; only a fresh login does.
        vault.deleteFails=false
        await restarted.signOut()
        let afterCleanup=AccountSession(vault:vault,transport:transport,barrier:FileSessionBarrier(directory:directory),stopManaged:{})
        XCTAssertFalse(afterCleanup.credentialCleanupFailed)
        XCTAssertEqual(try FileSessionBarrier(directory:directory).read(),.signedOut)
        do {_=try await afterCleanup.accessToken();XCTFail("cleanup is not login")}catch{}
        transport.nonce="wrong"
        do {try await afterCleanup.complete(code:"new",verifier:"new",nonce:"expected");XCTFail("nonce") }catch{}
        XCTAssertEqual(try FileSessionBarrier(directory:directory).read(),.signedOut)
        transport.nonce="expected"
        try await afterCleanup.complete(code:"new",verifier:"new",nonce:"expected")
        let afterLogin=AccountSession(vault:vault,transport:transport,barrier:FileSessionBarrier(directory:directory),stopManaged:{})
        let resumed=try await afterLogin.accessToken()
        XCTAssertEqual(resumed,"new")
        XCTAssertEqual(transport.refreshCalls,1)
    }

    @MainActor func testMissingMalformedAndUnreadableBarrierFailClosed() async throws {
        let directory=FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer {try? FileManager.default.removeItem(at:directory)}
        let barrier=FileSessionBarrier(directory:directory)
        let vault=MemoryVault();vault.values["managed-refresh"]=Data("retained".utf8)
        let transport=FakeTransport()
        // Missing metadata cannot adopt an old Keychain credential.
        let missing=AccountSession(vault:vault,transport:transport,barrier:barrier,stopManaged:{})
        do {_=try await missing.accessToken();XCTFail("missing barrier")}catch{}
        try barrier.write(.active)
        let file=directory.appendingPathComponent("session-state")
        let attributes=try FileManager.default.attributesOfItem(atPath:file.path)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue,0o600)
        let parentAttributes=try FileManager.default.attributesOfItem(atPath:directory.path)
        XCTAssertEqual((parentAttributes[.posixPermissions] as? NSNumber)?.intValue,0o700)
        XCTAssertEqual(try String(contentsOf:file,encoding:.utf8),"active")
        try Data("not-a-state".utf8).write(to:file)
        let malformed=AccountSession(vault:vault,transport:transport,barrier:barrier,stopManaged:{})
        do {_=try await malformed.accessToken();XCTFail("malformed barrier")}catch{}
        XCTAssertTrue(malformed.credentialCleanupFailed)
        try FileManager.default.setAttributes([.posixPermissions:0],ofItemAtPath:file.path)
        let unreadable=AccountSession(vault:vault,transport:transport,barrier:barrier,stopManaged:{})
        do {_=try await unreadable.accessToken();XCTFail("unreadable barrier")}catch{}
        XCTAssertTrue(unreadable.credentialCleanupFailed)
        XCTAssertEqual(transport.refreshCalls,0)
    }

    @MainActor func testFailedMarkerWriteStillDeletesRefreshBeforeRestart() async throws {
        let vault=MemoryVault();let transport=FakeTransport();let barrier=MemoryBarrier()
        let session=AccountSession(vault:vault,transport:transport,barrier:barrier,stopManaged:{})
        try await session.complete(code:"code",verifier:"verifier",nonce:"expected")
        barrier.writeFails=true;transport.revocationFails=true
        await session.signOut()
        XCTAssertEqual(vault.deleteCalls,1)
        XCTAssertNil(vault.values["managed-refresh"])
        XCTAssertTrue(session.credentialCleanupFailed)
        XCTAssertFalse(session.isSignedIn)
        let restarted=AccountSession(vault:vault,transport:transport,barrier:barrier,stopManaged:{})
        do {_=try await restarted.accessToken();XCTFail("deleted refresh must not be reused")}catch{}
        XCTAssertFalse(restarted.isSignedIn)
        XCTAssertEqual(transport.refreshCalls,0)
    }
    @MainActor func testBothLocalCleanupFailuresRemainExplicit() async throws {
        let vault=MemoryVault();let transport=FakeTransport();let barrier=MemoryBarrier()
        let session=AccountSession(vault:vault,transport:transport,barrier:barrier,stopManaged:{})
        try await session.complete(code:"code",verifier:"verifier",nonce:"expected")
        barrier.writeFails=true;vault.deleteFails=true;transport.revocationFails=true
        await session.signOut()
        XCTAssertEqual(vault.deleteCalls,1)
        XCTAssertTrue(session.credentialCleanupFailed)
        XCTAssertFalse(session.isSignedIn)
        do {_=try await session.accessToken();XCTFail("current session must stop")}catch{}
        // Neither durable store changed: do not claim cleanup survived restart.
        XCTAssertNotNil(vault.values["managed-refresh"])
        XCTAssertEqual(barrier.state,.active)
    }

}
final class MemoryVault: SessionVault {
    var values:[String:Data]=[:]
    var deleteFails=false;var deleteCalls=0
    func get(_ name:String)throws->Data?{values[name]}
    func put(_ name:String,value:Data)throws{values[name]=value}
    func delete(_ name:String)throws{deleteCalls+=1;if deleteFails{throw SessionError.unavailable};values.removeValue(forKey:name)}
}
@MainActor final class FakeTransport: SessionTransport {
    var nonce="expected";var rejected=false
    var revocationFails=false;var refreshCalls=0
    func exchange(code:String,verifier:String)async throws->TokenSet{TokenSet(accessToken:"access",refreshToken:"refresh",idToken:"id",expiresIn:300)}
    func refresh(_ token:String)async throws->TokenSet{refreshCalls+=1;if rejected{throw SessionError.unavailable};return TokenSet(accessToken:"new",refreshToken:"rotated",idToken:nil,expiresIn:300)}
    func verify(access:String,idToken:String)async throws->String{nonce}
    func identify(access:String)async throws->DeviceIdentity{if rejected{throw SessionError.unavailable};return DeviceIdentity(accountID:"account",deviceID:"device")}
    func revoke(access:String,deviceID:String)async throws{if revocationFails{throw SessionError.unavailable}}
    func deleteAccount(access:String)async throws{}
    func revokeRefresh(_ token:String)async throws{if revocationFails{throw SessionError.unavailable}}
}

final class MemoryBarrier: SessionBarrier {
    var state:SessionBarrierState = .signedOut
    var writeFails=false
    func read()throws->SessionBarrierState {state}
    func write(_ state:SessionBarrierState)throws {if writeFails{throw SessionError.unavailable};self.state=state}
}
