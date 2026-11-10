#!/usr/bin/env python3
"""LAB ONLY. Adds cargo feature `qa-session` to desktop_app: when built with it AND
MIXEL_QA_SESSION_FILE points at a JSON {access_token,refresh_token,expires_in}, "Sign in"
loads that session instead of opening the browser. Off by default; never build a release with it."""
import sys, pathlib
root=pathlib.Path(sys.argv[1]); d=root/'crates/desktop_app'
def sub(p,old,new,n=1):
    s=p.read_text()
    if new in s: return
    assert s.count(old)==n,(p,old[:50],s.count(old))
    p.write_text(s.replace(old,new))
sub(d/'Cargo.toml','testflight-controller = []\n','testflight-controller = []\nqa-session = []\n')
a=d/'src/account_auth.rs'
sub(a,'''    pub async fn begin(&self) -> Result<String, String> {
        let (client, provider, domain) = AuthClient::configured()?;
''','''    #[cfg(feature = "qa-session")]
    pub async fn begin_qa(&self, path: std::ffi::OsString) -> Result<(), String> {
        let (client, _, _) = AuthClient::configured()?;
        let tokens: Tokens = serde_json::from_slice(
            &std::fs::read(path).map_err(|_| "QA session file unreadable")?,
        )
        .map_err(|_| "QA session file invalid")?;
        if !tokens.valid() {
            return Err("QA session file invalid".into());
        }
        let generation = {
            let mut inner = self.inner.lock().await;
            inner.generation += 1;
            inner.status = Status {
                phase: "verifying".into(),
                message: "Loading lab session".into(),
                account: None,
                factors: Vec::new(),
            };
            inner.client = Some(client.clone());
            inner.generation
        };
        let this = self.clone();
        let task = tokio::spawn(async move {
            if let Err(error) = this.apply(generation, client, tokens, None).await {
                this.fail(generation, &error).await;
                return;
            }
            this.maintain(generation).await;
        });
        self.inner.lock().await.task = Some(task);
        Ok(())
    }
    pub async fn begin(&self) -> Result<String, String> {
        let (client, provider, domain) = AuthClient::configured()?;
''')
sub(d/'src/main.rs','''    main_window(&window)?;
    let url = state.begin().await?;
''','''    main_window(&window)?;
    #[cfg(feature = "qa-session")]
    if let Some(path) = std::env::var_os("MIXEL_QA_SESSION_FILE") {
        state.begin_qa(path).await?;
        return Ok(None);
    }
    let url = state.begin().await?;
''')
print('qa-session hook applied')
