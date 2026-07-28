package main

import (
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"os"
)

// tlsConfig builds mutual TLS against the panel's own CA.
//
// The CA is PINNED: RootCAs is replaced, not appended to, so the agent will
// not accept a certificate signed by any public authority even if one is
// mis-issued for whatever name or address the hub happens to answer on. The
// only thing it trusts is the CA the panel generated, which is the entire
// point of running a private one (see backend/app/pki.py).
//
// The agent also presents its own certificate. The hub requires it, so an
// agent without one is refused during the handshake, before it can send a
// single byte of protocol.
func tlsConfig(caPath, certPath, keyPath, serverName string, skipVerify bool) (*tls.Config, error) {
	cfg := &tls.Config{MinVersion: tls.VersionTLS12}

	if caPath != "" {
		pem, err := os.ReadFile(caPath)
		if err != nil {
			return nil, fmt.Errorf("read CA %s: %w", caPath, err)
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(pem) {
			return nil, fmt.Errorf("%s contains no usable certificate", caPath)
		}
		cfg.RootCAs = pool
	}

	if certPath != "" || keyPath != "" {
		if certPath == "" || keyPath == "" {
			return nil, fmt.Errorf("client cert and key must be given together")
		}
		pair, err := tls.LoadX509KeyPair(certPath, keyPath)
		if err != nil {
			return nil, fmt.Errorf("load client keypair: %w", err)
		}
		cfg.Certificates = []tls.Certificate{pair}
	}

	// The hub's certificate carries the addresses agents were told to dial, as
	// both IP and DNS SANs, so this normally stays empty and Go matches against
	// the dial address. Overriding it is for dialling through a relay whose
	// address is not on the certificate.
	cfg.ServerName = serverName

	// Only for bringing up a brand-new hub, never in a unit file. It disables
	// the pinning above, which is the whole security model here.
	cfg.InsecureSkipVerify = skipVerify //nolint:gosec // explicit, flag-guarded

	return cfg, nil
}
