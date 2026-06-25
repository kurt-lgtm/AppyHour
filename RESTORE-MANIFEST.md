# RESTORE MANIFEST (Tier E) — what each backup holds + how to restore it

Single source of truth for restoring AppyHour's LOCAL single-copy state after a
disk/machine loss. Pairs with the `appyhour-machine-restore` skill. Code itself
is on GitHub (Tier A) and is restored by cloning, not from these backups.

Backup producer: `scripts/backup_offsite.py` (weekly offsite + `--daily` local).
Decrypt tool: `scripts/decrypt_creds.py`. Off-machine secret you MUST keep: the
`AH_BACKUP_PASSPHRASE` (store in a password manager / phone — NOT on Drive).

| Asset | Tier / where | Backup artifact | Restore command |
|---|---|---|---|
| `shipping.db` (analytics/routing DB) | C daily → `E:\AppyHourBackups\daily\`; weekly → Drive | `shipping.daily-<date>.db` / `shipping.weekly-<date>.db` | copy newest snapshot → `%APPDATA%\AppyHour\shipping.db` |
| `~/.knowledge` vault + durable `~/.claude` (skills, hooks, agents, plans, scheduled-tasks, rules, commands, projects/*/memory, settings*.json) | weekly → Drive + `E:\...\weekly\` | `coldchain-knowledge-backup-<date>.zip` | unzip into `%USERPROFILE%\` (arcnames are home-relative) |
| Credentials (`%APPDATA%/AppyHour/*.json` + `*.txt` keys + `portal_profiles/` + repo-root `.env`) | weekly → Drive (encrypted) + `E:\...\creds\` | `coldchain-creds-backup-<date>.zip.enc` | `python scripts/decrypt_creds.py <file>.zip.enc <out_dir>` then place files back (see arcnames: bare name → `%APPDATA%\AppyHour\`, `repo/.env` → repo root) |
| Box-size lookup xlsx | weekly → Drive + `E:\...\reference\` | `coldchain-reference-backup-<date>.zip` | unzip → `%USERPROFILE%\Desktop\` (`box_simulation.py:20` reads it there) |
| Logic docs | weekly (redundant w/ git) | `coldchain-logic-backup-<date>.zip` | usually skip — pull from the AppyHour repo |
| SSH key | manual (ACL-blocked from automation) | `~/.ssh/id_ed25519` | copy from a safe location → `%USERPROFILE%\.ssh\` |

## Cadence (Windows Task Scheduler)
- **`AppyHour Daily Local Backup`** — daily 02:00 → `python scripts\backup_offsite.py --daily --dest E:\AppyHourBackups` (keeps 14).
- **`AppyHour Weekly Offsite Backup`** — Sun 02:00 → `python scripts\backup_offsite.py` (must run the **dev** repo copy, with `AH_BACKUP_PASSPHRASE` set in the task env).

## Verify (do NOT trust an unverified backup)
- Per run: a status line lands in `_outputs/logs/backup-<date>.log`; the run HARD-fails if the DB snapshot is empty, logs DEGRADED if knowledge/creds legs are empty.
- Quarterly dry-run: decrypt the newest `.enc`, unzip the newest knowledge bundle (file count > 0), and `PRAGMA integrity_check` a daily DB snapshot.
