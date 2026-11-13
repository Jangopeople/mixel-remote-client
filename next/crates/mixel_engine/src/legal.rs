//! The legal pages and the company data page on remote.mixel.ch.
/// The fixed portal address of a legal page (`terms`, `privacy`, `dpa`, `subprocessors`, `legal`, `company-data`).
pub fn legal_url(page: &str, lang: Option<&str>) -> Result<String, String> {
    let path = match page {
        "terms" | "privacy" | "dpa" | "subprocessors" => format!("/legal/{page}"),
        "legal" => "/legal".to_owned(),
        "company-data" => "/app/settings".to_owned(),
        _ => return Err("Unknown page".into()),
    };
    let lang = lang.filter(|value| ["en", "de", "fr", "it"].contains(value)).unwrap_or("en");
    Ok(format!("https://remote.mixel.ch{path}?lang={lang}"))
}

#[cfg(test)]
mod legal_tests {
    use super::legal_url;

    #[test]
    fn legal_pages_are_fixed_urls_on_the_portal() {
        assert_eq!(legal_url("terms", Some("de")).unwrap(), "https://remote.mixel.ch/legal/terms?lang=de");
        assert_eq!(legal_url("subprocessors", None).unwrap(), "https://remote.mixel.ch/legal/subprocessors?lang=en");
        assert_eq!(legal_url("company-data", Some("xx")).unwrap(), "https://remote.mixel.ch/app/settings?lang=en");
        assert!(legal_url("../../evil", None).is_err());
        assert!(legal_url("https://evil.example", None).is_err());
    }
}

