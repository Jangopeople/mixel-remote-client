//! How values are written on screen: Remote IDs, times, sizes, roles, plans and systems.
use crate::i18n::{t, tf};

/// A Remote ID in groups of three: 123 456 789.
pub fn remote_id(id: Option<u32>) -> String {
    let Some(id) = id else { return "— — —".into() };
    let digits = id.to_string();
    if digits.len() != 9 {
        return digits;
    }
    format!("{} {} {}", &digits[..3], &digits[3..6], &digits[6..])
}

/// A typed Remote ID: nine digits, the first not 0 (spaces allowed). `None` otherwise.
pub fn parse_remote_id(text: &str) -> Option<u32> {
    let digits: String = text.chars().filter(|c| !c.is_whitespace()).collect();
    if digits.len() != 9 || !digits.chars().all(|c| c.is_ascii_digit()) || digits.starts_with('0') {
        return None;
    }
    digits.parse().ok()
}

/// Groups the digits of a Remote ID as they are typed ("123456" → "123 456"); at most nine digits.
pub fn group_digits(text: &str) -> String {
    let mut grouped = String::new();
    for (index, digit) in text.chars().filter(char::is_ascii_digit).take(9).enumerate() {
        if index > 0 && index % 3 == 0 {
            grouped.push(' ');
        }
        grouped.push(digit);
    }
    grouped
}

/// Seconds since the Unix epoch of an ISO 8601 time ("2026-11-13T10:00:00.123+00:00", "…Z").
pub fn parse_time(iso: &str) -> Option<i64> {
    let iso = iso.trim();
    let (date, rest) = iso.split_once(['T', ' '])?;
    let mut parts = date.split('-');
    let (year, month, day): (i64, i64, i64) = (parts.next()?.parse().ok()?, parts.next()?.parse().ok()?, parts.next()?.parse().ok()?);
    let time_end = rest.find(['Z', '+', '-']).unwrap_or(rest.len());
    let (time, zone) = rest.split_at(time_end);
    let mut clock = time.split(':');
    let hour: i64 = clock.next()?.parse().ok()?;
    let minute: i64 = clock.next()?.parse().ok()?;
    let second: f64 = clock.next().unwrap_or("0").parse().ok()?;
    let offset = match zone.chars().next() {
        Some(sign @ ('+' | '-')) => {
            let digits: String = zone[1..].chars().filter(char::is_ascii_digit).collect();
            let hours: i64 = digits.get(..2)?.parse().ok()?;
            let minutes: i64 = digits.get(2..4).unwrap_or("00").parse().ok()?;
            let total = hours * 3600 + minutes * 60;
            if sign == '+' { total } else { -total }
        }
        _ => 0,
    };
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) {
        return None;
    }
    // Days from civil (Howard Hinnant's algorithm).
    let y = if month <= 2 { year - 1 } else { year };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(days * 86_400 + hour * 3600 + minute * 60 + second as i64 - offset)
}

pub fn now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|time| time.as_secs() as i64).unwrap_or(0)
}

/// "Last seen 5 minutes ago" and similar.
pub fn last_seen(iso: Option<&str>, now: i64) -> String {
    let Some(at) = iso.and_then(parse_time) else { return String::new() };
    let minutes = ((now - at).max(0) as f64 / 60.0).round() as i64;
    if minutes < 2 {
        return t("Last seen just now");
    }
    if minutes < 60 {
        return tf("Last seen %d minutes ago", &[&minutes]);
    }
    let hours = (minutes as f64 / 60.0).round() as i64;
    if hours < 24 {
        return if hours == 1 { t("Last seen 1 hour ago") } else { tf("Last seen %d hours ago", &[&hours]) };
    }
    let days = (hours as f64 / 24.0).round() as i64;
    if days == 1 { t("Last seen yesterday") } else { tf("Last seen %d days ago", &[&days]) }
}

/// A day as people write it in the shown language: "27 Nov 2026", "27. Nov. 2026", …
pub fn day(iso: Option<&str>) -> String {
    let Some(at) = iso.and_then(parse_time) else { return String::new() };
    let days = at.div_euclid(86_400);
    // Civil from days (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    let month = |names: [&str; 12]| names[(m - 1) as usize].to_owned();
    match crate::i18n::language() {
        "de" => format!("{d}. {} {y}", month(["Jan.", "Feb.", "März", "Apr.", "Mai", "Juni", "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."])),
        "fr" => format!("{d} {} {y}", month(["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."])),
        "it" => format!("{d} {} {y}", month(["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"])),
        _ => format!("{d} {} {y}", month(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])),
    }
}

pub fn bytes(count: u64) -> String {
    const UNITS: [&str; 5] = ["bytes", "KB", "MB", "GB", "TB"];
    if count < 1000 {
        return format!("{count} {}", UNITS[0]);
    }
    let mut value = count as f64;
    let mut unit = 0;
    while value >= 1000.0 && unit < UNITS.len() - 1 {
        value /= 1000.0;
        unit += 1;
    }
    let text = if value < 10.0 { format!("{value:.1}") } else { format!("{value:.0}") };
    let text = if matches!(crate::i18n::language(), "de" | "fr" | "it") { text.replace('.', ",") } else { text };
    format!("{text} {}", UNITS[unit])
}

/// The operating system as people say it.
pub fn os(os: &str) -> String {
    match os.to_ascii_lowercase().as_str() {
        "macos" => "macOS".into(),
        "windows" => "Windows".into(),
        "linux" => "Linux".into(),
        "ios" => "iPhone or iPad".into(),
        "android" => "Android".into(),
        "other" => t("phone or tablet"),
        _ => os.to_owned(),
    }
}

/// The symbolic icon of a system.
pub fn os_icon(os: &str) -> &'static str {
    match os.to_ascii_lowercase().as_str() {
        "macos" => "computer-symbolic",
        "ios" | "android" | "other" => "phone-symbolic",
        _ => "computer-symbolic",
    }
}

/// The person's role in a company, translated.
pub fn role(role: &str) -> String {
    match role {
        "owner" => t("Owner"),
        "admin" => t("Administrator"),
        "technician" => t("Technician"),
        "member" => t("Member"),
        _ => role.to_owned(),
    }
}

pub fn plan(plan: &str) -> String {
    match plan {
        "personal" => "Personal".into(),
        "solo" => "Solo".into(),
        "team" => "Team".into(),
        "business" => "Business".into(),
        "enterprise" => "Enterprise".into(),
        _ => {
            let mut chars = plan.chars();
            chars.next().map(|first| first.to_uppercase().chain(chars).collect()).unwrap_or_default()
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn remote_ids_are_grouped_and_parsed() {
        crate::i18n::set_choice("en");
        assert_eq!(remote_id(Some(123_456_789)), "123 456 789");
        assert_eq!(remote_id(None), "— — —");
        assert_eq!(remote_id(Some(42)), "42");
        assert_eq!(parse_remote_id("123 456 789"), Some(123_456_789));
        assert_eq!(parse_remote_id("012345678"), None);
        assert_eq!(parse_remote_id("12345678"), None);
        assert_eq!(parse_remote_id("12345678a"), None);
        assert_eq!(group_digits("1234567890"), "123 456 789");
        assert_eq!(group_digits("12a3 4"), "123 4");
    }

    #[test]
    fn times_are_parsed_in_utc() {
        assert_eq!(parse_time("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_time("2026-11-13T10:00:00.250+00:00"), Some(1_794_564_000));
        assert_eq!(parse_time("2026-11-13T12:00:00+02:00"), Some(1_794_564_000));
        assert_eq!(parse_time("2026-11-13 10:00:00"), Some(1_794_564_000));
        assert_eq!(parse_time("not a date"), None);
    }

    #[test]
    fn last_seen_rounds_to_people_units() {
        crate::i18n::set_choice("en");
        let base = parse_time("2026-11-13T10:00:00Z").unwrap();
        assert_eq!(last_seen(Some("2026-11-13T09:59:30Z"), base), "Last seen just now");
        assert_eq!(last_seen(Some("2026-11-13T09:55:00Z"), base), "Last seen 5 minutes ago");
        assert_eq!(last_seen(Some("2026-11-13T09:00:00Z"), base), "Last seen 1 hour ago");
        assert_eq!(last_seen(Some("2026-11-12T10:00:00Z"), base), "Last seen yesterday");
        assert_eq!(last_seen(Some("2026-11-10T10:00:00Z"), base), "Last seen 3 days ago");
        assert_eq!(last_seen(None, base), "");
    }

    #[test]
    fn days_sizes_and_names() {
        crate::i18n::set_choice("en");
        assert_eq!(day(Some("2026-11-27T10:00:00Z")), "27 Nov 2026");
        assert_eq!(day(Some("2024-02-29T23:00:00Z")), "29 Feb 2024");
        assert_eq!(bytes(512), "512 bytes");
        assert_eq!(bytes(1_500_000), "1.5 MB");
        assert_eq!(bytes(25_000_000), "25 MB");
        assert_eq!(plan("business"), "Business");
        assert_eq!(plan("custom"), "Custom");
        assert_eq!(os("windows"), "Windows");
        assert_eq!(role("technician"), "Technician");
    }
}
