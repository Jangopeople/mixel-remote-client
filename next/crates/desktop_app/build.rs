fn main() {
    // App version for code that runs without a window (the agent, the always-on host): the
    // tauri CLI passes config overrides in TAURI_CONFIG; otherwise tauri.conf.json applies.
    println!("cargo:rerun-if-env-changed=TAURI_CONFIG");
    println!("cargo:rerun-if-changed=tauri.conf.json");
    let version = |text: String| {
        serde_json::from_str::<serde_json::Value>(&text)
            .ok()
            .and_then(|value| value.get("version").and_then(|v| v.as_str()).map(str::to_owned))
    };
    let app_version = std::env::var("TAURI_CONFIG")
        .ok()
        .and_then(version)
        .or_else(|| std::fs::read_to_string("tauri.conf.json").ok().and_then(version))
        .unwrap_or_else(|| "0.0.0".into());
    println!("cargo:rustc-env=MIXEL_APP_VERSION={app_version}");
    if matches!(
        std::env::var("CARGO_CFG_TARGET_OS").as_deref(),
        Ok("windows" | "linux")
    ) {
        println!("cargo:rerun-if-env-changed=MIXEL_DESKTOP_SERVICE_CONFIG");
        let mut bytes = Vec::new();
        if let Some(path) = std::env::var_os("MIXEL_DESKTOP_SERVICE_CONFIG") {
            use std::io::Read;
            let path = std::path::PathBuf::from(path);
            println!("cargo:rerun-if-changed={}", path.display());
            std::fs::File::open(&path)
                .expect("Cannot open the public desktop service configuration")
                .take(32769)
                .read_to_end(&mut bytes)
                .expect("Cannot read the public desktop service configuration");
            assert!(
                !bytes.is_empty() && bytes.len() <= 32768,
                "Invalid desktop service configuration size"
            );
            bytes = validated_public_config(&bytes);
        }
        let out = std::path::PathBuf::from(std::env::var_os("OUT_DIR").expect("OUT_DIR required"));
        std::fs::write(out.join("desktop-service-config.json"), bytes)
            .expect("Cannot prepare the desktop service configuration resource");
    }
    let frontend = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../frontends/desktop");
    println!(
        "cargo:rerun-if-changed={}",
        frontend.join("../../../branding/icon-256.png").display()
    );
    for name in [
        "main.ts",
        "i18n.ts",
        "i18n-strings.ts",
        "capture.ts",
        "capture.html",
        "capture-panel.html",
        "remote.ts",
        "devices.ts",
        "assistant.ts",
        "enrollment.ts",
        "files.ts",
        "chat.ts",
        "session.ts",
        "workspace.ts",
        "viewer.ts",
        "viewer.html",
        "fixture.ts",
        "index.html",
        "fixture.html",
        "style.css",
        "fixture.css",
        "tsconfig.json",
        "package.json",
        "pnpm-lock.yaml",
        "build.mjs",
    ] {
        println!("cargo:rerun-if-changed={}", frontend.join(name).display());
    }
    let status = std::process::Command::new(if cfg!(windows) { "pnpm.cmd" } else { "pnpm" })
        .arg("--dir")
        .arg(&frontend)
        .args(["run", "build"])
        .status()
        .expect("pnpm is required to build the TypeScript frontend");
    assert!(
        status.success(),
        "Desktop TypeScript build failed; install pinned frontend dependencies with pnpm install --frozen-lockfile"
    );
    tauri_build::build();
}

fn validated_public_config(bytes: &[u8]) -> Vec<u8> {
    use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
    let config: serde_json::Value =
        serde_json::from_slice(bytes).expect("Invalid public desktop service configuration");
    let fields = config
        .as_object()
        .expect("Expected a desktop configuration object");
    const ALLOWED: &[&str] = &[
        "schema",
        "identity_url",
        "public_key",
        "signaling_url",
        "authority_url",
        "authority_keys",
        "oauth_provider",
        "sso_domain",
        "relay_only",
        "customer_signup",
    ];
    assert!(
        fields.keys().all(|key| ALLOWED.contains(&key.as_str())),
        "Unsupported public desktop configuration fields"
    );
    let key = fields
        .get("public_key")
        .and_then(serde_json::Value::as_str)
        .unwrap_or_default();
    let parts: Vec<_> = key.split('.').collect();
    let anonymous = parts.len() == 3
        && parts.iter().all(|part| !part.is_empty())
        && URL_SAFE_NO_PAD
            .decode(parts[1])
            .ok()
            .and_then(|data| serde_json::from_slice::<serde_json::Value>(&data).ok())
            .is_some_and(|claims| {
                claims.get("role").and_then(serde_json::Value::as_str) == Some("anon")
            });
    assert!(
        key.starts_with("sb_publishable_") || anonymous,
        "Only an anonymous or publishable key can be embedded"
    );
    serde_json::to_vec(&config).expect("Cannot serialize the public desktop service configuration")
}
