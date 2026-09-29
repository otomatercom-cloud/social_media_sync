# Social Media Sync (Odoo 19)

Depends on `custom_leads_19`. Install: copy to addons path, restart, Apps → Update Apps List → install.

## How the sync stays light
* First run: reads header + rows in batches of 500 (configurable), commits after each batch.
* Next runs: asks Google **only** for rows after `Last Synced Row` (`export?format=csv&range=A{n}:ZZ{m}` for public sheets, `values/Tab!A{n}:ZZ{m}` for Service Account). Nothing new = one tiny request, no log, no lead search.
* Edit window: last N rows (default 50) are re-read; a per-column fingerprint tells which cells changed and **only those fields** are written to the lead (phone / Ad ID edits are ignored).
* Same phone + same ad is processed once. Same phone + new ad, or existing e-mail → `Re-Attempt` (otomater.lead.reattempt, Pending Review).
* Tracker `otm_sm_sync_row`: one row per sheet row, no create/write metadata (~80 bytes). Logs: only runs that did something, purged after 24 h on every cron tick.
* Error rows keep the reason (Error Rows button). Fix the cause (e.g. add the Ad ID) and press **Retry Error Rows**.

## Sheet requirements
* Share: "Anyone with the link – Viewer" (public mode) or a Service Account with viewer access.
* Format the **Ad ID** column as *Plain text* (numbers become 1.2E+17 otherwise – such rows are rejected with a clear message).
* Rows should be appended (new leads at the bottom). Rows inserted in the middle of already-synced rows are not seen.
