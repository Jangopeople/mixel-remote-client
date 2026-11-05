fn main() {
    let frontend = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../frontends/desktop");
    for name in [
        "main.ts",
        "session.ts",
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
    let status = std::process::Command::new("pnpm")
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
