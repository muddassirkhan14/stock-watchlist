Optional build-time trust store for corporate TLS inspection

Place one or more root CA certificates here as .crt or .pem files (PEM-encoded
content is fine; use a .crt filename if unsure). They are copied into the
image at build time and "update-ca-certificates" is run so pip AND runtime
Python (requests to Yahoo Finance) trust your organisation's chain.

Do not commit private CAs to a public repo if policy forbids it — use the
runtime ./certs mount in docker-compose instead (see main README "Docker").
