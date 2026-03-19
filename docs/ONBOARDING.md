# Onboarding Flow

## Overview

Onboarding is a guided first-run flow in the UI that helps users:

1. Configure an AI agent app
2. Configure a Git app and sync docs
3. Infer products from Git activity
4. Run staged DevOps summaries for resources, products, and environments

Onboarding state is persisted on API side (in the built-in `devops-pass-ai` application settings), not in browser-only state.

Status values:

- `not_started`
- `in_progress`
- `skipped`
- `completed`

## Persistence Model

Onboarding state is stored in the built-in app (`app_id = devops-pass-ai`, `doc_type = dop_app`) under settings keys:

- `onboarding.status`
- `onboarding.updated_at`

When onboarding is skipped, a "Resume Onboarding" button is shown in UI header.

## Plugin Discovery Rules

Onboarding uses plugin metadata from `plugins/*/app.yaml`:

- `category: ai-agents` for step 1
- `category: git` for step 2
- `check_script` for test-before-add behavior

Plugin registry exposes these fields through `/api/plugin-apps`.

## Application Test Contract

Before adding an app that defines `check_script`, UI requires a successful test.

### API Endpoint

`POST /api/applications/test`

Request:

```json
{
  "plugin_key": "gitlab",
  "app_id": "my-gitlab",
  "settings": {
    "gitlab.server": "https://gitlab.example.com",
    "gitlab.token": "..."
  }
}
```

Response:

```json
{
  "status": "success",
  "message": "Authenticated as john.doe"
}
```

`status` is `success` or `failed`.

### Script Contract

`check_script` must point to a Python file exporting:

```python
def do_test(dop_app):
    ...
```

Where `dop_app.settings` contains values typed in the Add Application dialog.

Accepted return types:

- `dict` with `status` and `message`
- `bool`
- `str`
- `dop.error("...")` / `DopError`

## Implemented Check Scripts

### GitLab

File: `plugins/gitlab/docs/check.py`

Behavior:

- validates `gitlab.server` and `gitlab.token`
- calls `GET {server}/api/v4/user` with `PRIVATE-TOKEN`
- returns success with resolved username, or failure message

### GitHub Copilot

File: `plugins/github-copilot/docs/check.py`

Behavior:

- validates `copilot-cli.key`
- runs `copilot-cli auth status` (and `--json` variant)
- passes token through `COPILOT_GITHUB_TOKEN`
- returns success on command success, otherwise failure

## UI Stages

## Stage 1: Configure AI Agent

- Lists apps with `category = ai-agents`
- User opens existing Add Application dialog
- If app has `check_script`, user must click Test and pass
- At least one AI-agent app is required to proceed

## Stage 2: Configure Git

- Lists apps with `category = git`
- Same Test-before-Add behavior
- At least one git app is required to proceed
- After adding, docs refresh jobs are triggered for all doc types of that app

## Stage 3: Analyze Git Activity

Uses existing chat API (no special onboarding job type):

1. Create or reuse chat thread named `Onboarding`
2. Send onboarding analysis prompt
3. Show debug logs from the resulting chat job
4. Expect final JSON written to `/tmp/onboarding.json`

If `/tmp/onboarding.json` is missing after success, UI sends a follow-up message asking the agent to write the JSON to that path.

### Prompt Output Requirement

Agent is asked to:

- return JSON in chat
- also write final JSON array to `/tmp/onboarding.json`

Expected JSON structure:

```json
[
  {
    "name": "name",
    "id": "product_id",
    "related_resources": [
      { "git_repo": "repo_url" }
    ]
  }
]
```

### Product Selection UI

User sees checkbox selection for:

- products
- nested related resources per product

When confirmed, products are created using existing `/api/products` API.

Resource linking currently resolves repositories via explicit allowlist rule:

- GitLab repos only (`doc_type = gitlab_repos`)

## Stage 4: Initial Summary Pipeline

After products are created, UI builds dependency jobs using existing job APIs.

Per selected product:

1. Run `devops_summary` for each linked resource
2. Run `devops_summary` for product (`dop_product`) with dependencies on all resource jobs
3. Run `devops_summary` for each product env (`dop_env`) with dependency on product summary job

UI shows three stage lists:

- Resources Summary
- Products Summary
- Envs Summary

Each job is clickable to open existing job log dialog.

Onboarding is marked `completed` only when all stage-4 jobs finish successfully.

## Job System Dependencies Used

Onboarding relies on dependency-capable job APIs:

- `POST /api/jobs/doc-action`
- `POST /api/jobs/docs-refresh`

Supported payload fields used by onboarding:

- `depends_on_job_ids`
- `workflow_id`
- `max_parallel`

Jobs can be in `blocked` while waiting for dependencies or workflow slots.

## No Dedicated Onboarding API

By design, onboarding orchestration uses existing APIs:

- applications APIs
- chat APIs
- products APIs
- jobs APIs

No custom onboarding endpoint set was added.

## Related Files

- `ui/src/app/app.component.ts`
- `ui/src/app/app.component.html`
- `ui/src/app/app.component.css`
- `ui/src/app/api.service.ts`
- `api/main.py`
- `api/plugins.py`
- `api/schemas.py`
- `plugins/gitlab/docs/check.py`
- `plugins/github-copilot/docs/check.py`

## Notes / Limitations

- Product-resource resolution in onboarding currently targets GitLab repos only.
- `/tmp/onboarding.json` is a shared path; newest run overwrites previous content.
- Copilot check requires `copilot-cli` binary available in runtime environment.
- GitLab check requires network access to configured GitLab server.
