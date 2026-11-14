//! Session reports of the company: who connected to which computer, when, how long, attended or
//! unattended, with notes (add or edit) and a CSV or PDF export (the desktop app's and portal's layout). Owners and administrators see everyone's
//! sessions, technicians their own (the service decides).
use super::{dialogs, widgets::*};
use crate::{
    app::{App, State},
    format,
    i18n::{t, tf},
    logic,
    models::SessionRow,
    report_export::{self, Labels, PdfTexts},
};
use adw::prelude::*;
use gtk::glib;
use std::{cell::RefCell, rc::Rc};

/// The last report read (or why it could not be read); none before the first load.
type Loaded = Rc<RefCell<Option<Result<Vec<SessionRow>, String>>>>;
/// The period of the report read (the typed days, YYYY-MM-DD; empty when open-ended).
type Period = Rc<RefCell<(String, String)>>;

pub struct ReportsPage {
    pub widget: gtk::ScrolledWindow,
    body: gtk::Box,
    key: RefCell<String>,
    from: gtk::Entry,
    to: gtk::Entry,
    rows: Loaded,
    period: Period,
    list: gtk::Box,
}

fn today() -> (String, String) {
    let now = glib::DateTime::now_local().ok();
    let to = now.as_ref().and_then(|day| day.format("%Y-%m-%d").ok()).map(|text| text.to_string()).unwrap_or_default();
    let from = now.as_ref().and_then(|day| day.format("%Y-%m-01").ok()).map(|text| text.to_string()).unwrap_or_default();
    (from, to)
}

impl ReportsPage {
    pub fn new(_app: &Rc<App>) -> Self {
        let (widget, body) = page(&t("Session reports"), "");
        let (from, to) = today();
        let from_entry = entry("2026-11-01");
        from_entry.set_text(&from);
        from_entry.set_hexpand(false);
        from_entry.set_width_chars(12);
        let to_entry = entry("2026-11-30");
        to_entry.set_text(&to);
        to_entry.set_hexpand(false);
        to_entry.set_width_chars(12);
        Self { widget, body, key: RefCell::new(String::new()), from: from_entry, to: to_entry, rows: Rc::new(RefCell::new(None)), period: Rc::default(), list: vbox(12) }
    }

    pub fn render(&self, app: &Rc<App>, state: &State) {
        let company = &state.company.data;
        let key = format!("{:?}", (state.signed_in(), company.tenant().map(|tenant| tenant.tenant_id.clone()), company.overview.as_ref().and_then(|overview| overview.features.clone())));
        if *self.key.borrow() == key {
            return;
        }
        self.key.replace(key);
        keep_focus(&[self.from.clone().upcast(), self.to.clone().upcast()], || self.build(app, state));
        if company.tenant().is_some() && self.rows.borrow().is_none() {
            self.load(app);
        }
    }

    fn build(&self, app: &Rc<App>, state: &State) {
        let body = &self.body;
        clear_below_title(body);
        let company = &state.company.data;
        if company.tenant().is_none() {
            let status = adw::StatusPage::builder()
                .icon_name("x-office-spreadsheet-symbolic")
                .title(t("Session reports are part of company plans"))
                .description(t("Create a company under Account to see who connected to which computer, when and for how long."))
                .build();
            body.append(&status);
            return;
        }
        let plan_off = company.overview.as_ref().and_then(|overview| overview.features.as_ref()).and_then(|features| features.get("reports").copied()) == Some(false);
        if plan_off {
            append_message(body, &t("Session reports need a paid plan. Choose a plan in the portal to see them."), false);
            let a = app.clone();
            let plans = link(&t("Choose a plan"), move || a.open_uri("https://remote.mixel.ch"));
            body.append(&plans);
        }
        let filters = hbox(10);
        filters.append(&field(&t("From"), &adopt(&self.from)));
        filters.append(&field(&t("To"), &adopt(&self.to)));
        let a = app.clone();
        let me = self.handle();
        let show = primary(&t("Show"), move || me.load(&a));
        show.set_valign(gtk::Align::End);
        filters.append(&show);
        filters.append(&spacer());
        for kind in [Export::Csv, Export::Pdf] {
            let (a, rows, period) = (app.clone(), self.rows.clone(), self.period.clone());
            let label = if kind == Export::Csv { t("Export CSV…") } else { t("Export PDF…") };
            let export = button(&label, move || export(&a, &rows, &period, kind));
            export.set_valign(gtk::Align::End);
            filters.append(&export);
        }
        body.append(&filters);
        body.append(&self.list);
        self.show_rows(app);
    }

    /// A cheap handle for callbacks (the page itself lives in the window).
    fn handle(&self) -> Rc<Handle> {
        Rc::new(Handle { from: self.from.clone(), to: self.to.clone(), rows: self.rows.clone(), period: self.period.clone(), list: self.list.clone() })
    }

    fn load(&self, app: &Rc<App>) {
        self.handle().load(app);
    }

    fn show_rows(&self, app: &Rc<App>) {
        self.handle().show(app);
    }
}

struct Handle {
    from: gtk::Entry,
    to: gtk::Entry,
    rows: Loaded,
    period: Period,
    list: gtk::Box,
}

impl Handle {
    fn load(self: &Rc<Self>, app: &Rc<App>) {
        let (from, to) = (self.from.text().to_string(), self.to.text().to_string());
        let (from_day, to_day) = (logic::report_day(&from), logic::report_day(&to));
        if (!from.trim().is_empty() && from_day.is_none()) || (!to.trim().is_empty() && to_day.is_none()) || from_day.as_ref().zip(to_day.as_ref()).is_some_and(|(a, b)| a > b) {
            self.rows.replace(Some(Err(t("Enter the period as YYYY-MM-DD, the start before the end."))));
            self.show(app);
            return;
        }
        let period = (from_day.clone().unwrap_or_default(), to_day.clone().unwrap_or_default());
        // The end day counts in full.
        let to_next = to_day.and_then(|day| format::parse_time(&format!("{day}T00:00:00Z"))).map(|at| {
            let next = at + 86_400;
            glib::DateTime::from_unix_utc(next).ok().and_then(|day| day.format("%Y-%m-%d").ok()).map(|text| text.to_string()).unwrap_or_default()
        });
        clear(&self.list);
        self.list.append(&spinner_line(&t("Loading the session report…")));
        let (a, me) = (app.clone(), self.clone());
        app.spawn(async move {
            let result = a.load_report(from_day, to_next).await;
            me.period.replace(period);
            me.rows.replace(Some(result));
            me.show(&a);
        });
    }

    fn show(self: &Rc<Self>, app: &Rc<App>) {
        clear(&self.list);
        let rows = self.rows.borrow().clone();
        match rows {
            None => {}
            Some(Err(error)) => append_message(&self.list, &error, true),
            Some(Ok(rows)) if rows.is_empty() => self.list.append(&dim(&t("No sessions in this period."))),
            Some(Ok(rows)) => {
                let total: i64 = rows.iter().map(|row| row.duration_seconds).sum();
                self.list.append(&dim(&tf("%d sessions · %@ in total", &[&rows.len(), &logic::duration(total)])));
                let list = gtk::ListBox::new();
                list.add_css_class("boxed-list");
                list.set_selection_mode(gtk::SelectionMode::None);
                for row in rows {
                    let started = format::day(Some(&row.started_at));
                    let time = format::parse_time(&row.started_at)
                        .and_then(|at| glib::DateTime::from_unix_local(at).ok())
                        .and_then(|day| day.format("%H:%M").ok())
                        .map(|text| text.to_string())
                        .unwrap_or_default();
                    let who = row.technician_name.clone().or(row.technician_email.clone()).unwrap_or_else(|| t("Deleted account"));
                    let kind = if row.kind == "unattended" { t("Unattended") } else { t("Attended") };
                    let length = if row.ended_at.is_none() { format!("{} · {}", logic::duration(row.duration_seconds), t("running")) } else { logic::duration(row.duration_seconds) };
                    let title = format!("{} · {}", row.device_name, format::remote_id(Some(row.connection_id)));
                    let mut subtitle = format!("{started} {time} · {who} · {kind} · {length}");
                    if row.colleague {
                        subtitle.push_str(&format!(" · {}", t("Joined as colleague")));
                    }
                    if let Some(note) = row.note.as_ref().filter(|note| !note.is_empty()) {
                        subtitle.push_str(&format!("\n{note}"));
                    }
                    let item = adw::ActionRow::builder().title(glib::markup_escape_text(&title).as_str()).subtitle(glib::markup_escape_text(&subtitle).as_str()).build();
                    item.set_subtitle_lines(4);
                    let (a, me, session, current) = (app.clone(), self.clone(), row.session_id.clone(), row.note.clone().unwrap_or_default());
                    let edit = button(&if current.is_empty() { t("Add note") } else { t("Edit note") }, move || {
                        let Some(parent) = a.ui.borrow().as_ref().map(|ui| ui.window.clone()) else { return };
                        let (a2, me2, session2) = (a.clone(), me.clone(), session.clone());
                        dialogs::form(&parent, &t("Session note"), &t("An empty note removes it."), &[(&t("Note"), &current)], &t("Save note"), false, Rc::new(move |values| {
                            let (a, me, session) = (a2.clone(), me2.clone(), session2.clone());
                            Box::pin(async move {
                                a.session_note(Some(session), None, values[0].clone()).await?;
                                me.load(&a);
                                Ok(())
                            })
                        }));
                    });
                    edit.set_valign(gtk::Align::Center);
                    item.add_suffix(&edit);
                    list.append(&item);
                }
                self.list.append(&list);
            }
        }
    }
}

#[derive(Clone, Copy, PartialEq)]
enum Export {
    Csv,
    Pdf,
}

fn labels() -> Labels {
    Labels {
        technician: t("Technician"),
        email: t("E-mail"),
        device: t("Device"),
        remote_id: t("Remote ID"),
        start: t("Start"),
        end: t("End"),
        duration: t("Duration"),
        duration_seconds: t("Duration (seconds)"),
        kind: t("Type"),
        colleague: t("Joined as colleague"),
        note: t("Note"),
        session_id: t("Session ID"),
        attended: t("Attended"),
        unattended: t("Unattended"),
        running: t("Running"),
        yes: t("Yes"),
        no: t("No"),
    }
}

/// A time in this computer's zone with `pattern` (strftime), or empty.
fn local(iso: &str, pattern: &str) -> String {
    format::parse_time(iso)
        .and_then(|at| glib::DateTime::from_unix_local(at).ok())
        .and_then(|day| day.format(pattern).ok())
        .map(|text| text.to_string())
        .unwrap_or_default()
}

/// "13 Nov 2026 19:04" in the app's language (the day as everywhere else in the app).
fn when(iso: &str) -> String {
    let day = format::parse_time(iso).and_then(|at| glib::DateTime::from_unix_local(at).ok()).and_then(|day| day.format("%Y-%m-%dT12:00:00Z").ok());
    let day = day.map(|day| format::day(Some(day.as_str()))).unwrap_or_default();
    format!("{day} {}", local(iso, "%H:%M")).trim().to_owned()
}

fn export(app: &Rc<App>, rows: &Loaded, period: &Period, kind: Export) {
    let Some(Ok(rows)) = rows.borrow().clone() else { return };
    let Some(shell) = app.shell() else { return };
    let (from, to) = period.borrow().clone();
    let company = {
        let state = app.state.borrow();
        let tenant = state.company.data.tenant().cloned().unwrap_or_default();
        let slug = if tenant.slug.is_empty() { tenant.name.clone() } else { tenant.slug.clone() };
        (tenant.name, slug)
    };
    let now = glib::DateTime::now_utc().map(|at| at.to_unix()).unwrap_or_default();
    let (count, seconds) = report_export::totals(&rows, now);
    let totals = tf("%d sessions · %@ in total", &[&count, &report_export::format_duration(seconds)]);
    let first = rows.iter().map(|row| row.started_at.as_str()).min().unwrap_or_default().get(..10).unwrap_or_default().to_owned();
    let last = rows.iter().map(|row| row.started_at.as_str()).max().unwrap_or_default().get(..10).unwrap_or_default().to_owned();
    let (from, to) = (if from.is_empty() { first } else { from }, if to.is_empty() { last } else { to });
    let (content, extension) = match kind {
        Export::Csv => (report_export::report_csv(&rows, &labels(), now, &|iso: &str| local(iso, "%Y-%m-%d %H:%M")), "csv"),
        Export::Pdf => {
            let page = |page: usize, pages: usize| tf("Page %d of %d", &[&page, &pages]);
            let day = |text: &str| format::day(Some(&format!("{text}T12:00:00Z")));
            let generated_at = glib::DateTime::now_utc().ok().and_then(|at| at.format_iso8601().ok()).map(|text| text.to_string()).unwrap_or_default();
            let texts = PdfTexts {
                company: company.0.clone(),
                title: t("Session report"),
                range: format!("{} – {}", day(&from), day(&to)),
                generated: tf("Generated %@", &[&when(&generated_at)]),
                totals,
                page: &page,
                labels: labels(),
                when: &when,
                now,
            };
            (report_export::report_pdf(&rows, &texts), "pdf")
        }
    };
    let name = report_export::report_file_name(&company.1, &from, &to, extension);
    app.spawn(async move {
        if let Some(path) = shell.choose_save(&name).await {
            match std::fs::write(&path, content) {
                Ok(()) => shell.notify(&t("Session report saved")),
                Err(_) => shell.notify(&t("The file could not be saved")),
            }
        }
    });
}
