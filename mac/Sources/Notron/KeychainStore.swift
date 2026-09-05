import Foundation
import Security
import LocalAuthentication

/// P06 must package/sign this bridge with a stable identity before enabling it.
/// Secrets are never command arguments, standard output, or diagnostic messages.
struct KeychainStore {
    static let service = "com.m1labs.notron"
    static let names: Set<String> = ["storage-key", "nebius-api-key", "tavily-api-key", "development-nebius-api-key"]
    enum Failure: Error { case unavailable, invalidRequest }

    private func query(_ name: String) throws -> [String: Any] {
        guard Self.names.contains(name) else { throw Failure.invalidRequest }
        let context = LAContext()
        context.interactionNotAllowed = true
        return [kSecClass as String: kSecClassGenericPassword,
                kSecAttrService as String: Self.service,
                kSecAttrAccount as String: name,
                kSecUseAuthenticationContext as String: context]
    }

    func get(_ name: String) throws -> Data? {
        var attributes = try query(name)
        attributes[kSecReturnData as String] = true
        attributes[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(attributes as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw Failure.unavailable }
        return data
    }

    func put(_ name: String, value: Data) throws {
        guard !value.isEmpty else { throw Failure.invalidRequest }
        let attributes = try query(name)
        let update = [kSecValueData as String: value]
        let status = SecItemUpdate(attributes as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var create = attributes
            create[kSecValueData as String] = value
            create[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            guard SecItemAdd(create as CFDictionary, nil) == errSecSuccess else { throw Failure.unavailable }
        } else if status != errSecSuccess { throw Failure.unavailable }
    }

    func delete(_ name: String) throws {
        let status = SecItemDelete(try query(name) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw Failure.unavailable }
    }

    static func serve(fd: Int32) {
        guard fd > 2 else { return }
        let channel = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
        var response: [String: String] = ["status": "unavailable"]
        do {
            var bytes = Data()
            while let block = try channel.read(upToCount: 4096), !block.isEmpty {
                bytes.append(block)
                guard bytes.count <= 65536 else { throw Failure.invalidRequest }
            }
            guard let request = try JSONSerialization.jsonObject(with: bytes) as? [String: String],
                  let name = request["name"], let operation = request["operation"] else {
                throw Failure.invalidRequest
            }
            let store = KeychainStore()
            switch operation {
            case "get":
                if let value = try store.get(name) {
                    response = ["status": "ok", "value": value.base64EncodedString()]
                } else { response = ["status": "missing"] }
            case "put":
                guard let text = request["value"], let value = Data(base64Encoded: text) else {
                    throw Failure.invalidRequest
                }
                try store.put(name, value: value)
                response = ["status": "ok"]
            case "delete":
                try store.delete(name)
                response = ["status": "ok"]
            default: throw Failure.invalidRequest
            }
        } catch { /* Fixed status only; never expose Security errors or payloads. */ }
        if let bytes = try? JSONSerialization.data(withJSONObject: response) {
            try? channel.write(contentsOf: bytes)
        }
    }
}

#if NOTRON_KEYCHAIN_HELPER
@main
struct KeychainBridge {
    static func main() {
        let arguments = CommandLine.arguments
        guard arguments.count == 3, arguments[1] == "--credential-fd",
              let fd = Int32(arguments[2]), fd > 2 else { return }
        KeychainStore.serve(fd: fd)
    }
}
#endif
