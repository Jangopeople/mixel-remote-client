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
        "printing.ts",
        "colleagues.ts",
        "enrollment.ts",
        "files.ts",
        "chat.ts",
        "session.ts",
        "workspace.ts",
        "report-export.ts",
        "reports.ts",
        "i18n-reports.ts",
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
