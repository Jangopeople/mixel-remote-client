//! What the Devices console lists: the sidebar view (all, online now, favourites, recent, one group),
//! the chips (Online, an operating system, Unattended, Update available), the search text and the sort
//! order, as plain data without GTK. The device table asks `visible` and `counts`; the sidebar numbers
//! come from `counts`. Unit-tested here.

/// The sidebar's Views and Groups.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub enum View {
    #[default]
    All,
    Online,
    Favourites,
    Recent,
    Group(String),
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Sort {
    /// Online first, then by name (the default, "Sorted by status").
    #[default]
    Status,
    Name,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Filters {
    pub view: View,
    pub search: String,
    pub online_only: bool,
    pub unattended_only: bool,
    pub update_only: bool,
    /// `macos`, `windows` or `linux`.
    pub os: Option<&'static str>,
    pub sort: Sort,
}

/// One device as the table filters it.
#[derive(Clone, Debug, Default)]
pub struct Row {
    pub id: u32,
    pub name: String,
    pub os: String,
    pub online: bool,
    pub favourite: bool,
    /// Position among the person's last connections (0 = newest).
    pub recent: Option<usize>,
    pub group: Option<String>,
    pub unattended: bool,
    pub update: bool,
    pub note: String,
}

/// The chip an operating-system name belongs to.
pub fn os_family(os: &str) -> &'static str {
    let os = os.to_ascii_lowercase();
    if os.contains("mac") || os.contains("darwin") || os.contains("ios") {
        "macos"
    } else if os.contains("win") {
        "windows"
    } else if os.is_empty() || os.contains("android") {
        "other"
    } else {
        "linux"
    }
}

fn digits(text: &str) -> String {
    text.chars().filter(char::is_ascii_digit).collect()
}

pub fn matches(filters: &Filters, row: &Row) -> bool {
    let in_view = match &filters.view {
        View::All => true,
        View::Online => row.online,
        View::Favourites => row.favourite,
        View::Recent => row.recent.is_some(),
        View::Group(id) => row.group.as_deref() == Some(id.as_str()),
    };
    if !in_view
        || (filters.online_only && !row.online)
        || (filters.unattended_only && !row.unattended)
        || (filters.update_only && !row.update)
    {
        return false;
    }
    if filters
        .os
        .is_some_and(|family| os_family(&row.os) != family)
    {
        return false;
    }
    let search = filters.search.trim().to_lowercase();
    if search.is_empty() {
        return true;
    }
    let wanted_digits = digits(&search);
    row.name.to_lowercase().contains(&search)
        || row.note.to_lowercase().contains(&search)
        || row.os.to_lowercase().contains(&search)
        || (!wanted_digits.is_empty()
            && wanted_digits.len() == search.chars().filter(|c| !c.is_whitespace()).count()
            && row.id.to_string().contains(&wanted_digits))
}

/// The rows to show, sorted.
pub fn visible<'a>(filters: &Filters, rows: &'a [Row]) -> Vec<&'a Row> {
    let mut shown: Vec<&Row> = rows.iter().filter(|row| matches(filters, row)).collect();
    if filters.view == View::Recent {
        shown.sort_by_key(|row| row.recent.unwrap_or(usize::MAX));
        return shown;
    }
    match filters.sort {
        Sort::Status => shown.sort_by(|a, b| {
            b.online
                .cmp(&a.online)
                .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
        }),
        Sort::Name => shown.sort_by_key(|row| row.name.to_lowercase()),
    }
    shown
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Counts {
    pub all: usize,
    pub online: usize,
    pub favourites: usize,
    pub recent: usize,
}

pub fn counts(rows: &[Row]) -> Counts {
    Counts {
        all: rows.len(),
        online: rows.iter().filter(|row| row.online).count(),
        favourites: rows.iter().filter(|row| row.favourite).count(),
        recent: rows.iter().filter(|row| row.recent.is_some()).count(),
    }
}

/// The number of devices in a group.
pub fn group_count(rows: &[Row], group: &str) -> usize {
    rows.iter()
        .filter(|row| row.group.as_deref() == Some(group))
        .count()
}

/// `a` is an older app version than `b` ("2.0.3" < "2.0.18"; anything that is not a number sorts first).
pub fn version_older(a: &str, b: &str) -> bool {
    let parts = |text: &str| -> Vec<u64> {
        text.split(['.', '-', '+'])
            .map(|part| part.parse::<u64>().unwrap_or(0))
            .collect()
    };
    let (mut a, mut b) = (parts(a), parts(b));
    let length = a.len().max(b.len());
    a.resize(length, 0);
    b.resize(length, 0);
    a < b
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: u32, name: &str, os: &str, online: bool) -> Row {
        Row {
            id,
            name: name.into(),
            os: os.into(),
            online,
            ..Row::default()
        }
    }
    fn rows() -> Vec<Row> {
        vec![
            Row {
                favourite: true,
                group: Some("front".into()),
                unattended: true,
                ..row(731_204_118, "Reception PC", "windows", true)
            },
            Row {
                recent: Some(0),
                ..row(268_455_901, "Anna MacBook", "macos", true)
            },
            row(482_913_557, "Tech Mac", "macos", true),
            Row {
                update: true,
                note: "Warehouse scanner".into(),
                ..row(519_003_442, "build-agent", "linux", false)
            },
        ]
    }

    #[test]
    fn views_count_and_filter() {
        let all = rows();
        let c = counts(&all);
        assert_eq!((c.all, c.online, c.favourites, c.recent), (4, 3, 1, 1));
        let mut f = Filters::default();
        assert_eq!(visible(&f, &all).len(), 4);
        f.view = View::Online;
        assert_eq!(visible(&f, &all).len(), 3);
        f.view = View::Favourites;
        assert_eq!(visible(&f, &all)[0].name, "Reception PC");
        f.view = View::Recent;
        assert_eq!(visible(&f, &all)[0].name, "Anna MacBook");
        f.view = View::Group("front".into());
        assert_eq!(visible(&f, &all).len(), 1);
        assert_eq!(group_count(&all, "front"), 1);
    }

    #[test]
    fn chips_combine() {
        let all = rows();
        let f = Filters {
            os: Some("macos"),
            online_only: true,
            ..Filters::default()
        };
        assert_eq!(visible(&f, &all).len(), 2);
        let f = Filters {
            unattended_only: true,
            ..Filters::default()
        };
        assert_eq!(visible(&f, &all)[0].id, 731_204_118);
        let f = Filters {
            update_only: true,
            ..Filters::default()
        };
        assert_eq!(visible(&f, &all)[0].name, "build-agent");
        let f = Filters {
            os: Some("linux"),
            online_only: true,
            ..Filters::default()
        };
        assert!(visible(&f, &all).is_empty());
    }

    #[test]
    fn search_reads_name_id_and_note() {
        let all = rows();
        let by = |text: &str| Filters {
            search: text.into(),
            ..Filters::default()
        };
        assert_eq!(visible(&by("recep"), &all).len(), 1);
        assert_eq!(visible(&by("731 204"), &all).len(), 1);
        assert_eq!(visible(&by("731204118"), &all).len(), 1);
        assert_eq!(visible(&by("scanner"), &all)[0].id, 519_003_442);
        assert!(visible(&by("nothing"), &all).is_empty());
        assert_eq!(visible(&by("  "), &all).len(), 4);
    }

    #[test]
    fn sort_by_status_puts_online_first() {
        let all = rows();
        let names: Vec<_> = visible(&Filters::default(), &all)
            .iter()
            .map(|row| row.name.as_str())
            .collect();
        assert_eq!(
            names,
            ["Anna MacBook", "Reception PC", "Tech Mac", "build-agent"]
        );
        let f = Filters {
            sort: Sort::Name,
            ..Filters::default()
        };
        let names: Vec<_> = visible(&f, &all)
            .iter()
            .map(|row| row.name.as_str())
            .collect();
        assert_eq!(
            names,
            ["Anna MacBook", "build-agent", "Reception PC", "Tech Mac"]
        );
    }

    #[test]
    fn os_family_and_versions() {
        assert_eq!(os_family("macOS"), "macos");
        assert_eq!(os_family("Windows 11"), "windows");
        assert_eq!(os_family("ubuntu"), "linux");
        assert_eq!(os_family("android"), "other");
        assert!(version_older("2.0.3", "2.0.18"));
        assert!(!version_older("2.0.18", "2.0.18"));
        assert!(!version_older("2.1", "2.0.19"));
        assert!(version_older("1.9.8", "2.0.0"));
    }
}
