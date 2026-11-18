//! Company switches for the remote tools, sent by the broker (`ServerMessage::ToolsPolicy`).
//! Everything is OFF until the broker says otherwise. Lab override (qa-session builds only):
//! `MIXEL_TOOLS_POLICY=terminal,terminal_elevate,tasks,tasks_unattended,scripts`.
use std::sync::Mutex;

#[derive(Clone, Copy, Default, Debug, PartialEq, Eq)]
pub struct Policy {
    pub terminal: bool,
    pub terminal_elevate: bool,
    pub tasks: bool,
    pub tasks_unattended: bool,
    pub scripts: bool,
}
impl Policy {
    pub fn parse(list: &str) -> Self {
        let has = |name: &str| list.split(',').any(|part| part.trim() == name);
        let terminal = has("terminal");
        let tasks = has("tasks");
        Self {
            terminal,
            terminal_elevate: terminal && has("terminal_elevate"),
            tasks,
            tasks_unattended: tasks && has("tasks_unattended"),
            scripts: has("scripts"),
        }
    }
    /// Combines the sub-switches with their parents (elevation needs the terminal, and so on).
    pub fn normalized(self) -> Self {
        Self {
            terminal_elevate: self.terminal && self.terminal_elevate,
            tasks_unattended: self.tasks && self.tasks_unattended,
            ..self
        }
    }
}

static CURRENT: Mutex<Option<Policy>> = Mutex::new(None);

/// The broker's latest switches.
pub fn set(policy: Policy) {
    if let Ok(mut slot) = CURRENT.lock() {
        *slot = Some(policy.normalized());
    }
}
/// The switches in force: the broker's, else (qa-session builds) the lab variable, else all off.
pub fn get() -> Policy {
    if let Some(policy) = CURRENT.lock().ok().and_then(|slot| *slot) {
        return policy;
    }
    #[cfg(feature = "qa-session")]
    if let Ok(list) = std::env::var("MIXEL_TOOLS_POLICY") {
        return Policy::parse(&list);
    }
    Policy::default()
}
/// Whether the broker has spoken about tools (audit records go to it only then).
pub fn broker_knows_tools() -> bool {
    CURRENT.lock().ok().is_some_and(|slot| slot.is_some())
}
#[cfg(test)]
pub fn reset() {
    if let Ok(mut slot) = CURRENT.lock() {
        *slot = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_is_off_and_children_need_parents() {
        assert_eq!(Policy::default(), Policy { terminal: false, terminal_elevate: false, tasks: false, tasks_unattended: false, scripts: false });
        let p = Policy::parse("terminal_elevate,tasks_unattended");
        assert!(!p.terminal_elevate && !p.tasks_unattended);
        let p = Policy::parse("terminal, terminal_elevate,tasks,scripts");
        assert!(p.terminal && p.terminal_elevate && p.tasks && !p.tasks_unattended && p.scripts);
        let n = Policy { terminal: false, terminal_elevate: true, ..Default::default() }.normalized();
        assert!(!n.terminal_elevate);
    }
}
