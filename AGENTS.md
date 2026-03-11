# Agent Working Rules (plan2table)

## Temporary outputs
- For non-sensitive temporary artifacts (debug CSV, debug images, ad-hoc logs), use `/tmp`.
- Do not create or use a repo-local temp folder unless explicitly requested.

## Local E2E fixtures
- When the user asks for E2E validation, use local fixtures under:
  - input PDFs: `.local/e2e-fixtures/input`
  - expected outputs (CSV/JSON): `.local/e2e-fixtures/expected`
- Treat `.local/` as local-only data. Do not commit any files from `.local/`.
- E2E reports should compare actual output vs expected output and explicitly list:
  - matched cases
  - mismatches (row/column/value-level where possible)
  - missing/extra rows

## Secrets and credentials
- Never write secrets to files (including `/tmp` and this repository).
- When using 1Password CLI, read secrets directly into an environment variable for the current command only.
- Do not print secret values to stdout/stderr.
- After secret-based commands, clear related environment variables in the same shell session when possible.
- Never commit secrets, secret-derived files, or credential dumps.

## 1Password workflow for GCP (Vertex AI + Vision API)
- Prerequisite: user signs in to 1Password first.
- Account: `my.1password.com`
- Project ID: `op://antas/me check service account json key file/add more/project ID`
- Service account JSON (Vertex + Vision 共通): `op://antas/me check service account json key file/me-check-487106-61fe11f85a91.json`
- Recommended login command:
  - `eval "$(op signin --account my.1password.com)"`
- Recommended secret read: use `op read '...'` for project ID and for the JSON key; pass to `GOOGLE_CLOUD_PROJECT` and `GCP_SERVICE_ACCOUNT_KEY`. See Makefile.
- Use it directly for command execution in the same shell, and avoid persisting the value.

## If file-based secret handling is unavoidable
- Ask for explicit user approval first.
- Use restrictive permissions (`600`) and delete immediately after use.
