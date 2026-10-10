#!/usr/bin/env python3
"""Require authenticated encryption for pinned Mixel ID/relay connections."""
import os
from pathlib import Path


def replace_once(source: str, old: str, new: str, description: str) -> str:
    if new in source:
        return source
    if source.count(old) != 1:
        raise RuntimeError(f"Pinned upstream secure handshake changed: {description}")
    return source.replace(old, new, 1)


def patch_client(source: str) -> str:
    source = replace_once(source,
        """    ) -> ResultType<Option<Vec<u8>>> {
        let rs_pk = get_rs_pk(if key.is_empty() {""",
        """    ) -> ResultType<Option<Vec<u8>>> {
        // A branded ID/relay session must authenticate the peer and encrypt
        // before any login, video, input or file-transfer message is sent.
        // Direct IP/LAN sessions take a separate path before this function.
        let pinned_mixel_support = crate::is_custom_client()
            && !(if key.is_empty() { config::RS_PUB_KEY } else { key }).is_empty();
        let rs_pk = get_rs_pk(if key.is_empty() {""",
        "peer pin policy")
    source = replace_once(source,
        """            None => {
                // send an empty message out in case server is setting up secure and waiting for first message""",
        """            None => {
                if pinned_mixel_support {
                    bail!("Handshake failed: missing or invalid authenticated peer identity");
                }
                // send an empty message out in case server is setting up secure and waiting for first message""",
        "missing authenticated peer identity")
    source = replace_once(source,
        """                                log::error!("Handshake failed: sign failure");
                                conn.send(&Message::new()).await?;""",
        """                                if pinned_mixel_support {
                                    bail!("Handshake failed: authenticated peer ID mismatch");
                                }
                                log::error!("Handshake failed: sign failure");
                                conn.send(&Message::new()).await?;""",
        "wrong signed ephemeral peer ID")
    source = replace_once(source,
        """                            // fall back to non-secure connection in case pk mismatch""",
        """                            if pinned_mixel_support {
                                bail!("Handshake failed: invalid signed peer encryption key");
                            }
                            // fall back to non-secure connection in case pk mismatch""",
        "invalid signed ephemeral key")
    source = replace_once(source,
        """                        log::error!("Handshake failed: invalid message type");
                        conn.send(&Message::new()).await?;""",
        """                        if pinned_mixel_support {
                            bail!("Handshake failed: invalid peer encryption message type");
                        }
                        log::error!("Handshake failed: invalid message type");
                        conn.send(&Message::new()).await?;""",
        "unexpected peer message")
    source = replace_once(source,
        """                    log::error!("Handshake failed: invalid message format");
                    conn.send(&Message::new()).await?;""",
        """                    if pinned_mixel_support {
                        bail!("Handshake failed: invalid peer encryption message format");
                    }
                    log::error!("Handshake failed: invalid message format");
                    conn.send(&Message::new()).await?;""",
        "malformed peer message")
    source = replace_once(source,
        """        Ok(option_pk)
    }

    /// Request a relay connection""",
        """        if pinned_mixel_support && !conn.is_secured() {
            bail!("Handshake failed: peer encryption was not established");
        }
        Ok(option_pk)
    }

    /// Request a relay connection""",
        "authenticated peer encryption invariant")
    return source


def patch_common(source: str) -> str:
    signature = "pub async fn secure_tcp(conn: &mut Stream, key: &str) -> ResultType<()> {"
    start = source.index(signature)
    end = source.index("\n#[inline]\nfn get_pk", start)
    original = source[start:end]
    modified = replace_once(original,
        signature,
        signature + "\n    let pinned_mixel_support = crate::is_custom_client() && !key.is_empty();",
        "rendezvous pin policy")
    modified = replace_once(modified,
        "    if use_ws() {\n        return Ok(());\n    }",
        """    // A concurrent network attempt can enable HTTPS after this native
    // TCP socket was created. Only the actual WebSocket transport may skip
    // its redundant rendezvous encryption handshake for pinned support.
    if if pinned_mixel_support {
        matches!(conn, Stream::WebSocket(_))
    } else {
        use_ws()
    } {
        return Ok(());
    }""",
        "actual rendezvous transport rather than mutable network preference")
    modified = replace_once(modified,
        """                    _ => {}
                }
            }
        }
        _ => {}
    }
    Ok(())""",
        """                    _ => {
                        if pinned_mixel_support {
                            bail!("Handshake failed: invalid rendezvous encryption message type");
                        }
                    }
                }
            } else if pinned_mixel_support {
                bail!("Handshake failed: invalid rendezvous encryption message format");
            }
        }
        _ => {
            if pinned_mixel_support {
                bail!("Handshake failed: rendezvous encryption key was not received");
            }
        }
    }
    if pinned_mixel_support && !conn.is_secured() {
        bail!("Handshake failed: rendezvous encryption was not established");
    }
    Ok(())""",
        "rendezvous malformed/missing key exchange")
    return source[:start] + modified + source[end:]


HOST_WRAPPERS = """pub async fn create_tcp_connection(
    server: ServerPtr,
    stream: Stream,
    addr: SocketAddr,
    secure: bool,
    control_permissions: Option<ControlPermissions>,
) -> ResultType<()> {
    let require_authenticated = crate::is_custom_client();
    if require_authenticated && !secure {
        bail!("Handshake failed: Mixel ID/relay support requires authenticated encryption");
    }
    create_tcp_connection_(server, stream, addr, secure, control_permissions, require_authenticated).await
}

// Only the explicitly enabled direct IP/LAN listener may use upstream's
// plaintext protocol. Relay requests cannot select this bypass.
pub async fn create_direct_tcp_connection(
    server: ServerPtr,
    stream: Stream,
    addr: SocketAddr,
    secure: bool,
    control_permissions: Option<ControlPermissions>,
) -> ResultType<()> {
    create_tcp_connection_(server, stream, addr, secure, control_permissions, false).await
}

async fn create_tcp_connection_(
    server: ServerPtr,
    stream: Stream,
    addr: SocketAddr,
    secure: bool,
    control_permissions: Option<ControlPermissions>,
    require_authenticated: bool,
) -> ResultType<()> {"""


def patch_server(source: str) -> str:
    original_signature = """pub async fn create_tcp_connection(
    server: ServerPtr,
    stream: Stream,
    addr: SocketAddr,
    secure: bool,
    control_permissions: Option<ControlPermissions>,
) -> ResultType<()> {"""
    source = replace_once(source, original_signature, HOST_WRAPPERS, "separate host ID/relay and direct listener policy")
    start = source.index("async fn create_tcp_connection_(")
    end = source.index("\npub async fn accept_connection(", start)
    original = source[start:end]
    modified = replace_once(original,
        """    let (sk, pk) = Config::get_key_pair();
    if secure && pk.len() == sign::PUBLICKEYBYTES && sk.len() == sign::SECRETKEYBYTES {""",
        """    let (sk, pk) = Config::get_key_pair();
    if require_authenticated
        && (!secure || pk.len() != sign::PUBLICKEYBYTES || sk.len() != sign::SECRETKEYBYTES)
    {
        bail!("Handshake failed: Mixel host identity is not ready for authenticated encryption");
    }
    if secure && pk.len() == sign::PUBLICKEYBYTES && sk.len() == sign::SECRETKEYBYTES {""",
        "host signing identity availability")
    modified = replace_once(modified,
        """                        } else if pk.asymmetric_value.is_empty() {
                            Config::set_key_confirmed(false);""",
        """                        } else if pk.asymmetric_value.is_empty() {
                            if require_authenticated {
                                bail!("Handshake failed: Mixel peer requested an unencrypted session");
                            }
                            Config::set_key_confirmed(false);""",
        "host rejects empty peer encryption key")
    modified = replace_once(modified,
        """                    } else {
                        log::error!("Handshake failed: invalid message type");""",
        """                    } else {
                        if require_authenticated {
                            bail!("Handshake failed: invalid Mixel peer encryption message type");
                        }
                        log::error!("Handshake failed: invalid message type");""",
        "host rejects wrong peer encryption message")
    modified = replace_once(modified,
        """    #[cfg(target_os = "macos")]
    {""",
        """    if require_authenticated && !stream.is_secured() {
        bail!("Handshake failed: Mixel host encryption was not established");
    }

    #[cfg(target_os = "macos")]
    {""",
        "host authenticated encryption invariant")
    return source[:start] + modified + source[end:]


def patch_direct_listener(source: str) -> str:
    start = source.index("async fn direct_server(")
    end = source.index("\nenum Sink", start)
    original = source[start:end]
    modified = replace_once(original,
        "crate::server::create_tcp_connection(",
        "crate::server::create_direct_tcp_connection(",
        "only explicit direct listener may bypass authenticated support")
    return source[:start] + modified + source[end:]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    repository = Path(os.environ.get("RDREPO", root / "rustdesk")).resolve()
    for relative, patch in (("src/client.rs", patch_client), ("src/common.rs", patch_common),
                            ("src/server.rs", patch_server), ("src/rendezvous_mediator.rs", patch_direct_listener)):
        path = repository / relative
        original = path.read_text(encoding="utf-8")
        modified = patch(original)
        if original != modified:
            path.write_text(modified, encoding="utf-8")
    print("   installed fail-closed authenticated encryption for pinned Mixel support")


if __name__ == "__main__":
    main()
