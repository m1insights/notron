import Foundation
import NotronCore

// Entry point for the credential helper.
//
// This binary is the only thing allowed to read Notron's Keychain items for the
// Python side, and it is deliberately its own process: Python bridges to it over
// an inherited socket (`--credential-fd`), so no secret ever travels through an
// argument, an environment variable, standard output, or a file. `notron/bundle.py`
// locates this executable inside the signed app bundle and verifies the
// signature before anything is asked of it; the path never comes from model
// input or an environment override.
//
// Anything that is not the exact expected invocation exits non-zero and says
// nothing. A helper that explains itself on a bad argument is a helper that can
// be probed.

let arguments = CommandLine.arguments

guard arguments.count == 3,
      arguments[1] == "--credential-fd",
      let descriptor = Int32(arguments[2]),
      descriptor > 2 else {
    exit(2)
}

KeychainStore.serve(fd: descriptor)
