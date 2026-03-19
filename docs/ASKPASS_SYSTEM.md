# Git and SSH AskPass System

## Overview

DOP uses a unified askpass flow for both HTTPS Git auth and SSH auth prompts.
When a plugin action runs Git/SSH commands inside the API container, prompts are forwarded to the UI via API endpoints.

This supports:
- Git HTTPS username/password prompts (`GIT_ASKPASS`)
- SSH username/password/passphrase prompts (`SSH_ASKPASS`)
- Session-only "save" behavior
- SSH key passphrase session save through `ssh-agent`

## Architecture

```
UI <-> API askpass endpoints <-> askpass.py <-> git/ssh process
                                      |
                                      +-> JobsManager prompt queue
                                      +-> optional ssh-add (save passphrase for current session)
```

## Runtime Components

### 1. AskPass script (`api/askpass.py`)

`askpass.py` is invoked by Git/SSH with a prompt string and does the following:
1. Reads `DOP_ASKPASS_JOB_ID`
2. Sends `POST /api/askpass/request` with `{job_id, prompt}`
3. Polls `GET /api/askpass/answer/{request_id}` until answer is available (or timeout)
4. Prints the answer to stdout for Git/SSH to consume

### 2. Job askpass manager (`api/jobs.py`)

`JobsManager` stores active askpass requests in memory.

Each request includes:
- `request_id`
- `job_id`
- `prompt`
- `prompt_kind` (`username` | `password` | `ssh_passphrase`)
- `answer`

Behavior:
- `create_askpass_request` classifies prompt kind and creates request
- `get_pending_askpass_requests(job_id)` returns unanswered prompts for UI
- `answer_askpass_request` stores answer and optionally performs save logic
- `cancel_askpass_request` marks request as cancelled

### 3. API endpoints (`api/main.py`)

- `POST /api/askpass/request`
- `GET /api/jobs/{job_id}/askpass`
- `POST /api/askpass/answer/{request_id}`
- `POST /api/askpass/cancel/{request_id}`
- `GET /api/askpass/answer/{request_id}`

## Environment and Container Setup

### Askpass environment for jobs

For each action job, runtime command env includes:
- `GIT_ASKPASS=/workspace/api/askpass.py` (actual path resolved at runtime)
- `GIT_ASKPASS_PROMPT=echo`
- `SSH_ASKPASS=/workspace/api/askpass.py`
- `SSH_ASKPASS_REQUIRE=force`
- `DISPLAY=:0`
- `DOP_ASKPASS_JOB_ID=<job-id>`
- `DOP_ASKPASS_API_URL=<api-url>`
- `SSH_AUTH_SOCK` (if available in container env)

### API container SSH agent

The API container starts `ssh-agent` from `docker-entrypoint.sh` and keeps it available for the API process lifetime:
- Uses socket path `/tmp/dop-ssh-agent.sock` by default
- Exports `SSH_AUTH_SOCK` before starting uvicorn
- Reuses existing agent socket if already healthy

The Docker image includes `openssh-client` so `ssh-agent` and `ssh-add` are available.

## UI Behavior

The UI polls pending askpass requests and opens a dialog for the first pending prompt.

Prompt handling:
- `username` prompt -> text input
- `password` / `ssh_passphrase` prompt -> password input

Save checkbox behavior:
- Hidden for username prompts
- Visible for password and passphrase prompts
- Label for SSH passphrase prompts: `Save for current session (add key to ssh-agent)`
- Label for others: `Save for current session`

## Save Semantics (Session-only)

All askpass saves are in-memory for the current API process session only.
No askpass credential persistence to disk is used.

### For SSH key passphrase prompts

If user checks save:
1. DOP attempts `ssh-add` with the provided passphrase
2. If prompt contains explicit key path, DOP uses that key
3. For generic passphrase prompts, DOP tries default key candidates:
   - `~/.ssh/id_ed25519`
   - `~/.ssh/id_ecdsa`
   - `~/.ssh/id_rsa`
   - `~/.ssh/id_dsa`
4. If all candidates fail, API returns 400 and submission fails (request remains pending)

This is intentionally fail-fast to avoid claiming session save when `ssh-agent` could not load a key.

## End-to-End Flow

1. Plugin action runs (for example `git clone` with HTTPS or SSH)
2. Git/SSH invokes `askpass.py`
3. API stores askpass request
4. UI shows dialog, user submits or cancels
5. API stores answer (and optionally tries session-save logic)
6. askpass script receives answer and returns it to Git/SSH
7. Command continues or fails

## Error Cases

- User cancels prompt -> askpass returns cancel marker -> Git/SSH auth fails fast
- Askpass timeout -> script exits non-zero after timeout window
- Save with SSH passphrase fails (`ssh-add`) -> `POST /api/askpass/answer/{request_id}` returns HTTP 400
- Unknown request ID -> 404

## Security Notes

- Askpass values are never written to job logs
- Save mode is session-only (memory/agent lifetime)
- SSH passphrase save uses `ssh-agent` and is lost when container/API process restarts
- For production deployments, protect API transport appropriately

## Troubleshooting

### Askpass dialog does not appear
- Ensure job is running
- Verify `GET /api/jobs/{job_id}/askpass` returns pending requests
- Check browser console for polling errors

### SSH save fails with HTTP 400
- Verify `ssh-agent` is running in API container
- Verify `SSH_AUTH_SOCK` is present in API process env
- Ensure key exists (`~/.ssh/id_ed25519` etc. for generic prompts)
- Ensure passphrase is correct

### Git/SSH still prompts in terminal
- Ensure command is executed via `jobs.run_command(...)` or within job runtime env
- Verify `GIT_ASKPASS` / `SSH_ASKPASS` env vars are set for that process
