# Existing web deployment handoff

Checked 2026-11-26 (Asia/Shanghai).

- Existing platform documented in README: Streamlit Community Cloud.
- Existing repository: https://github.com/xlin3834/quant-signal-lab
- Existing configured branch per README: main; entry: quant_signal_lab_online/app.py.
- Main remains b0d6972. No production merge or deploy performed.
- Exact app URL is NOT recorded in this checkout. GitHub homepage is null; public deployments and commit statuses are empty. Do not guess a streamlit.app name.
- P0_AUDIT.md and P1_REPORT.md record that an earlier anonymous visit to the original URL redirected to Streamlit login (303), but do not retain that URL. This is historical evidence, not a current live test.

Next: use the owner's signed-in Streamlit Community Cloud dashboard at https://share.streamlit.io/ to select the EXISTING app for the above repo and record its actual URL, visibility, branch and entry path. Opening the dashboard is not deployment. Keep its current visibility unless explicitly approved.

The local feature branch feat/signal-lab-realtime-presets now contains realtime/presets plus the research/macro increment (65561d8). Preserve it and push that branch through an authorized authenticated Git path before changing production. Previous git push failed due to missing credentials; no new push attempted. GitHub connector read access works, but that does not authenticate the local git executable.

To update the SAME app without merging main, check whether its settings permit changing its source branch to the reviewed feature branch. If branch changes are not supported for an existing app, stop and obtain an explicit main-merge decision rather than recreating a website. Snapshot/backup any existing user_presets.json and paper SQLite to approved persistent storage before restart; Community Cloud local disk is not persistent. Do not dump secrets into logs or commit them.

Current research caveats: macro public /candles smoke test returned only 89 daily, 12 weekly, 4 monthly bars for BTC/ETH. The engine correctly reports insufficient weekly/quarterly history and has no closed yearly frame from that response. Full long-horizon macro validation and a live browser acceptance pass remain unfinished. Native App work has not been started.
