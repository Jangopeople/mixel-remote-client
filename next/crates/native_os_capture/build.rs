fn main() {
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rerun-if-changed=src/macos_login_keyboard.c");
        println!("cargo:rerun-if-changed=src/macos_controller_keychain.c");
        cc::Build::new()
            .file("src/macos_controller_keychain.c")
            .warnings_into_errors(true)
            .compile("mixel_controller_keychain");
        cc::Build::new()
            .file("src/macos_login_keyboard.c")
            .warnings_into_errors(true)
            .compile("mixel_login_keyboard");
        println!("cargo:rerun-if-changed=src/macos_guard.m");
        cc::Build::new()
            .file("src/macos_guard.m")
            .flag("-fobjc-arc")
            .flag("-mmacosx-version-min=13.0")
            .warnings_into_errors(true)
            .compile("mixel_guard");
        for framework in ["AppKit", "ApplicationServices", "CoreMedia", "ScreenCaptureKit", "CoreGraphics"] {
            println!("cargo:rustc-link-lib=framework={framework}");
        }
        println!("cargo:rustc-link-lib=framework=IOKit");
        println!("cargo:rustc-link-lib=framework=Security");
    }
}
