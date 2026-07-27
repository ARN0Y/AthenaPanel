package main

import "crypto/tls"

// tlsConfig is the client side of the hub connection.
//
// Phase 1 validates against the local node over loopback, where TLS buys
// nothing. The option exists now so that exposing the hub publicly in Phase 2
// is a config change on an already-tested path rather than new code written
// under pressure. Verification is on by default; -tls-skip-verify exists only
// for bringing a new hub up and should never appear in a unit file.
func tlsConfig(skipVerify bool) *tls.Config {
	return &tls.Config{
		MinVersion:         tls.VersionTLS12,
		InsecureSkipVerify: skipVerify, //nolint:gosec // guarded by an explicit flag
	}
}
