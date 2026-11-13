//! Where `--signin-enroll` gets the deploy token: from the `MIXEL_DEPLOY_TOKEN` environment
//! variable or, when that is not set, the first line on standard input. Never from the command
//! line: every user of the computer can read a process's arguments (`ps`, Task Manager) and the
//! shell keeps them in its history.
//!
//! The install scripts pipe the token in (`printf '%s\n' "$TOKEN" | mixel-remote --signin-enroll`);
//! the Windows command sets the variable just for the enrol step (`Start-Process` inherits it).

use std::io::BufRead;

/// The environment variable that carries the deploy token for `--signin-enroll`.
pub const ENV: &str = "MIXEL_DEPLOY_TOKEN";
/// What `--signin-enroll <token>` (the pre-0.1.22 form) says now.
pub const ARGUMENT_REFUSED: &str = "The deploy token is not accepted on the command line (other users could read it in the process list). Set MIXEL_DEPLOY_TOKEN or pipe the token in: printf '%s\\n' \"$TOKEN\" | mixel-remote --signin-enroll";
const MISSING: &str =
    "The deploy token is missing: set MIXEL_DEPLOY_TOKEN or pipe the token in on standard input";
const INVALID: &str = "The deploy token is missing or invalid";
/// Longest line read from standard input (a token is at most 100 characters).
const LINE_LIMIT: u64 = 512;

/// A deploy token: `mrt_` and 16 to 96 base64url characters (what the service creates).
pub fn valid(token: &str) -> bool {
    token.starts_with("mrt_")
        && (20..=100).contains(&token.len())
        && token
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
}

/// The token for `--signin-enroll`. Call it first thing in `main`, while the process has a single
/// thread: the variable is removed from this process's environment so no child inherits it.
pub fn read(argument: Option<&str>) -> Result<String, String> {
    let from_env = std::env::var(ENV).ok();
    if from_env.is_some() {
        // SAFETY: called from `main` before any other thread exists, so nothing reads the
        // environment while it changes.
        #[allow(unsafe_code)]
        unsafe {
            std::env::remove_var(ENV);
        }
    }
    let stdin = std::io::stdin();
    choose(
        argument,
        from_env,
        || std::io::IsTerminal::is_terminal(&stdin),
        stdin.lock(),
    )
}

/// Picks and checks the token: the variable if set, else one line of `input` (not a terminal:
/// nobody would see a prompt from a window-less step, and the input would be echoed).
fn choose(
    argument: Option<&str>,
    from_env: Option<String>,
    terminal: impl FnOnce() -> bool,
    input: impl BufRead,
) -> Result<String, String> {
    if argument.is_some() {
        return Err(ARGUMENT_REFUSED.into());
    }
    let raw = match from_env.filter(|value| !value.trim().is_empty()) {
        Some(value) => value,
        None => {
            if terminal() {
                return Err(MISSING.into());
            }
            let mut line = String::new();
            input
                .take(LINE_LIMIT)
                .read_line(&mut line)
                .map_err(|_| MISSING)?;
            if line.trim().is_empty() {
                return Err(MISSING.into());
            }
            line
        }
    };
    let token = raw.trim();
    if valid(token) {
        Ok(token.to_owned())
    } else {
        Err(INVALID.into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOKEN: &str = "mrt_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789-_";

    fn pick(
        argument: Option<&str>,
        env: Option<&str>,
        terminal: bool,
        input: &str,
    ) -> Result<String, String> {
        choose(
            argument,
            env.map(str::to_owned),
            || terminal,
            input.as_bytes(),
        )
    }

    #[test]
    fn reads_the_token_from_the_environment_first() {
        assert_eq!(
            pick(None, Some(TOKEN), false, "mrt_other_token_from_stdin_000").unwrap(),
            TOKEN
        );
        assert_eq!(
            pick(None, Some(&format!("  {TOKEN}\r\n")), true, "").unwrap(),
            TOKEN
        );
    }

    #[test]
    fn reads_one_line_from_standard_input_otherwise() {
        assert_eq!(
            pick(None, None, false, &format!("{TOKEN}\nsecond line\n")).unwrap(),
            TOKEN
        );
        assert_eq!(
            pick(None, Some(""), false, &format!("{TOKEN}\r\n")).unwrap(),
            TOKEN
        );
        assert_eq!(pick(None, None, false, TOKEN).unwrap(), TOKEN);
    }

    #[test]
    fn refuses_the_token_on_the_command_line() {
        assert_eq!(
            pick(Some(TOKEN), None, false, "").unwrap_err(),
            ARGUMENT_REFUSED
        );
        assert_eq!(
            pick(Some(TOKEN), Some(TOKEN), false, "").unwrap_err(),
            ARGUMENT_REFUSED
        );
    }

    #[test]
    fn does_not_wait_on_a_terminal_or_accept_an_empty_or_invalid_token() {
        assert_eq!(pick(None, None, true, TOKEN).unwrap_err(), MISSING);
        assert_eq!(pick(None, None, false, "").unwrap_err(), MISSING);
        assert_eq!(pick(None, None, false, "\n").unwrap_err(), MISSING);
        for bad in [
            "mri_AbCdEfGhIjKlMnOpQrSt",
            "mrt_short",
            "mrt_AbCdEfGhIjKlMnOp'; rm -rf /",
            "mrt_AbCdEf GhIjKlMnOpQrSt",
        ] {
            assert_eq!(
                pick(None, Some(bad), false, "").unwrap_err(),
                INVALID,
                "{bad}"
            );
            assert_eq!(pick(None, None, false, bad).unwrap_err(), INVALID, "{bad}");
        }
        let long = format!("mrt_{}", "a".repeat(2000));
        assert_eq!(pick(None, None, false, &long).unwrap_err(), INVALID);
    }
}
