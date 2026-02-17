# Agent Working Rules (plan2table)

## Temporary outputs
- For non-sensitive temporary artifacts (debug CSV, debug images, ad-hoc logs), use `/tmp`.
- Do not create or use a repo-local temp folder unless explicitly requested.

## Secrets and credentials
- Never write secrets to files (including `/tmp` and this repository).
- When using 1Password CLI, read secrets directly into an environment variable for the current command only.
- Do not print secret values to stdout/stderr.
- After secret-based commands, clear related environment variables in the same shell session when possible.
- Never commit secrets, secret-derived files, or credential dumps.

## 1Password workflow for Vision API
- Prerequisite: user signs in to 1Password first.
- Account: `my.1password.com`
- Secret reference: `op://antas/vision api me check/me-check-487106-03df4ceb885d.json`
- Recommended login command:
  - `eval "$(op signin --account my.1password.com)"`
- Recommended secret read command:
  - `VISION_SERVICE_ACCOUNT_KEY="$(op read 'op://antas/vision api me check/me-check-487106-03df4ceb885d.json')"`
- Use it directly for command execution in the same shell, and avoid persisting the value.

## If file-based secret handling is unavoidable
- Ask for explicit user approval first.
- Use restrictive permissions (`600`) and delete immediately after use.
