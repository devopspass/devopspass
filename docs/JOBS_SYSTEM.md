# Jobs System

## Overview

The jobs system is the backend execution layer for long-running work in DevOps Pass AI.

It is responsible for:

- creating job records for asynchronous work
- executing plugin fetchers and plugin actions outside the request/response path
- persisting job state, logs, and activity events in SQLite
- exposing job status and logs via API endpoints
- handling cancellation where supported
- supporting job dependencies and workflow-level concurrency limits

The implementation lives primarily in `api/jobs.py`, with persistence in `api/db.py` and API endpoints in `api/main.py`.

## Supported Job Types

The system currently supports these job types:

### `docs_refresh`

Loads documents for a specific plugin application and doc type by calling a plugin `get_docs(application_doc)` function.

Typical use:

- refresh GitLab projects
- refresh GitHub repositories
- refresh Confluence pages

### `doc_action`

Runs a plugin action against a specific stored document by calling a plugin `do_action(dop_app, doc, action_name)` function.

Typical use:

- clone repository
- generate `devops_summary`
- trigger custom integration-specific actions

### `chat_message`

Runs an AI chat message through the internal agent runner. This type is created internally by the chat API, but it now also supports the same dependency/workflow scheduling model as the other job types.

## High-Level Architecture

```
┌──────────────┐
│   API Call   │
│ create job   │
└──────┬───────┘
       │
       ▼
┌──────────────────────────────┐
│ JobsManager                  │
│ - validate request           │
│ - assign workflow_id         │
│ - resolve dependencies       │
│ - persist initial job state  │
└──────┬───────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ Scheduler                    │
│ - wait for dependencies      │
│ - enforce workflow slots     │
│ - start ready jobs           │
└──────┬───────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ Job Execution                │
│ - run plugin / agent logic   │
│ - write stdout/stderr logs   │
│ - write activity events      │
│ - update final status        │
└──────┬───────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ SQLite Persistence           │
│ - jobs                       │
│ - job_logs                   │
│ - job_agent_events           │
└──────────────────────────────┘
```

## Core Concepts

### Job

A job is a persisted execution unit with:

- unique `id`
- `job_type`
- lifecycle `status`
- timestamps
- summary/failure/result fields
- metadata specific to the job type
- optional dependency metadata

### Workflow

A workflow is a logical group of dependent jobs.

Each workflow has:

- `workflow_id`
- `workflow_max_parallel`

The workflow does not exist as a separate database table. It is represented by shared metadata on jobs.

### Dependency

A dependency means one job must not start until another job finishes successfully.

Dependencies are expressed by `depends_on_job_ids`.

Reverse links are tracked in `dependent_job_ids` so downstream jobs can be failed quickly when an upstream job fails or is cancelled.

## Job Lifecycle

Jobs move through these states:

### `queued`

The job exists and is ready to run as soon as the scheduler picks it up.

This usually means:

- it has no dependencies, or
- all dependencies are already satisfied, and
- a workflow execution slot is available

### `blocked`

The job exists but cannot start yet.

Common reasons:

- one or more dependencies are still incomplete
- the workflow has reached its `max_parallel` running limit

The reason is exposed via `blocking_reason`.

### `running`

The job has started execution.

At this point:

- `started_at` is set
- logs may be appended to `job_logs`
- activity events may be appended to `job_agent_events`

### `success`

The job completed successfully.

At this point:

- `finished_at` is set
- `summary` describes success
- `result` may contain structured output

### `failed`

The job failed.

At this point:

- `finished_at` is set
- `summary` contains a short failure summary
- `failure` may contain detailed traceback or failure text

Jobs may also enter `failed` without ever starting if an upstream dependency failed or was cancelled.

### `cancelled`

The job was cancelled by the user.

This is currently relevant for cancellable job types such as `chat_message`. For queued or blocked jobs that support cancellation, cancellation happens immediately without starting execution.

## Dependency Model

## Creation Rules

Dependencies are optional and are declared when creating a job.

Rules enforced by the API:

- all `depends_on_job_ids` must already exist
- all dependency jobs must belong to the same workflow
- cross-workflow dependencies are rejected
- if `workflow_id` is omitted, it is inherited from dependencies when possible
- if there are no dependencies and no explicit `workflow_id`, the server generates a new workflow id
- if `max_parallel` conflicts with an existing dependency workflow limit, the request is rejected
- cycles are rejected

## Scheduling Semantics

A dependent job starts only when all prerequisite jobs are in `success` state.

If any prerequisite becomes:

- `failed`
- `cancelled`

then downstream jobs that have not started are marked `failed` immediately.

This is intentional. The scheduler does not keep invalid branches waiting for manual recovery.

## Failure Propagation

Failure propagation is transitive for pending downstream jobs.

Example:

```
A -> B -> C
```

If `A` fails:

- `B` is marked `failed`
- `C` is marked `failed`

If `B` is already `running`, it is not forcibly interrupted by dependency propagation. Only not-yet-started downstream jobs are failed automatically.

## Cancellation Propagation

If an upstream job is cancelled before a downstream job starts:

- downstream pending jobs are marked `failed`
- the reason is recorded as blocked by a cancelled dependency

This matches the same branch-invalidation model used for normal failures.

## Workflow Concurrency

Each workflow has a `workflow_max_parallel` limit.

This controls how many jobs in the same workflow may be in `running` state at the same time.

### Default

The default value comes from environment variable:

`DOP_WORKFLOW_MAX_PARALLEL`

Default value:

`3`

### Behavior

If a job has all dependencies satisfied but the workflow is already at capacity, the job remains `blocked` with a `blocking_reason` like:

`Waiting for workflow slot (3/3)`

As soon as a running job in the same workflow finishes, the scheduler reevaluates blocked jobs and starts newly eligible ones.

## Persistence Model

The following SQLite tables are used:

### `jobs`

Stores the main job record.

Core columns include:

- `id`
- `job_type`
- `status`
- `created_at`
- `started_at`
- `finished_at`
- `app_doc_id`
- `app_id`
- `dop_app_name`
- `dop_app_icon`
- `doc_type`
- `doc_type_title`
- `doc_name`
- `summary`
- `failure`
- `result`
- `can_cancel`
- `cancel_requested`
- `metadata`

Dependency and workflow fields are stored inside `metadata`, not as top-level table columns.

This currently includes:

- `depends_on_job_ids`
- `dependent_job_ids`
- `workflow_id`
- `workflow_max_parallel`
- `blocking_reason`
- other job-type-specific fields such as `doc_id`, `action_name`, `thread_id`, `source`

### `job_logs`

Stores line-oriented stdout/stderr logs.

Each row includes:

- `job_id`
- `stream`
- `timestamp`
- `entry`

### `job_agent_events`

Stores structured activity events, mainly used by AI/chat jobs.

Each row includes:

- `job_id`
- `event_type`
- `text`
- `timestamp`

## Restart and Retention Behavior

### Restart Recovery

On API startup, incomplete jobs are marked failed.

This includes jobs previously in:

- `queued`
- `blocked`
- `running`

The summary is set to indicate the API restarted while the job was incomplete.

This prevents orphaned jobs from remaining indefinitely active after a restart.

### Retention

Old jobs are pruned based on creation time.

Retention is controlled by:

`DOP_JOBS_RETENTION_DAYS`

Default value:

`7`

### In-Memory Cache

`JobsManager` also maintains an in-memory job cache for recently loaded jobs. The database remains the source of truth for persisted history.

## API Endpoints

## Read APIs

### `GET /api/jobs`

Returns recent jobs ordered by `created_at DESC`.

### `GET /api/jobs/{job_id}`

Returns one job with logs and agent events.

### `GET /api/jobs/{job_id}/stream`

Server-Sent Events stream of agent activity. Primarily useful for chat jobs.

### `GET /api/jobs/{job_id}/askpass`

Returns pending askpass prompts for a job.

See [docs/ASKPASS_SYSTEM.md](./ASKPASS_SYSTEM.md) for the full credential prompt flow.

## Write APIs

### `POST /api/jobs/docs-refresh`

Creates a docs refresh job.

Body:

```json
{
  "app_doc_id": 12,
  "doc_type": "gitlab_repos",
  "depends_on_job_ids": [],
  "workflow_id": null,
  "max_parallel": 3
}
```

You may use `app_id` instead of `app_doc_id`.

### `POST /api/jobs/doc-action`

Creates a document action job.

Body:

```json
{
  "doc_id": 145,
  "action_name": "devops_summary",
  "depends_on_job_ids": [],
  "workflow_id": null,
  "max_parallel": 3
}
```

### `POST /api/jobs/{job_id}/cancel`

Requests cancellation for a job.

Behavior depends on the current state:

- queued or blocked cancellable jobs are cancelled immediately
- running cancellable jobs receive termination request if a process exists
- non-cancellable jobs return validation error

## Job Response Shape

Job responses include the standard fields below.

Example:

```json
{
  "id": "8d8d609a-cff9-49d9-b0b1-1e77a7154d75",
  "job_type": "doc_action",
  "status": "blocked",
  "created_at": "2026-03-18T09:15:12.120000+00:00",
  "started_at": null,
  "finished_at": null,
  "app_doc_id": 7,
  "app_id": "gitlab-main",
  "dop_app_name": "GitLab",
  "dop_app_icon": "fab fa-gitlab",
  "doc_type": "gitlab_repos",
  "doc_type_title": "DevOps Summary",
  "doc_name": "backend-service",
  "summary": null,
  "failure": null,
  "result": null,
  "can_cancel": false,
  "depends_on_job_ids": [
    "f4db0a2d-fdc0-4ec9-8af4-6fd11b628035"
  ],
  "dependent_job_ids": [],
  "workflow_id": "0d7650ae-e170-41db-b8a6-681d5b3f1570",
  "workflow_max_parallel": 3,
  "blocking_reason": "Waiting for dependencies: f4db0a2d-fdc0-4ec9-8af4-6fd11b628035",
  "action_name": "devops_summary",
  "logs": [],
  "agent_events": []
}
```

## Practical Dependency Example

The intended flow from the feature request looks like this:

1. create a mostly empty product and link resources to it
2. trigger `devops_summary` for each linked resource
3. trigger `devops_summary` for the product after all resource summaries succeed
4. trigger environment jobs after the product summary succeeds

Example:

### Step 1: create resource summary jobs

First resource job:

```http
POST /api/jobs/doc-action
```

```json
{
  "doc_id": 101,
  "action_name": "devops_summary",
  "max_parallel": 3
}
```

The response returns a generated `workflow_id`.

Create other resource jobs in the same workflow:

```json
{
  "doc_id": 102,
  "action_name": "devops_summary",
  "workflow_id": "<workflow-id-from-first-job>"
}
```

```json
{
  "doc_id": 103,
  "action_name": "devops_summary",
  "workflow_id": "<workflow-id-from-first-job>"
}
```

These jobs may run in parallel, up to `workflow_max_parallel`.

### Step 2: create product summary job depending on resource summaries

```json
{
  "doc_id": 999,
  "action_name": "devops_summary",
  "depends_on_job_ids": [
    "<resource-job-1>",
    "<resource-job-2>",
    "<resource-job-3>"
  ],
  "workflow_id": "<workflow-id-from-first-job>"
}
```

This job will remain `blocked` until all resource jobs are `success`.

### Step 3: create environment jobs depending on the product summary job

```json
{
  "doc_id": 2001,
  "action_name": "deploy_env",
  "depends_on_job_ids": ["<product-summary-job-id>"],
  "workflow_id": "<workflow-id-from-first-job>"
}
```

```json
{
  "doc_id": 2002,
  "action_name": "deploy_env",
  "depends_on_job_ids": ["<product-summary-job-id>"],
  "workflow_id": "<workflow-id-from-first-job>"
}
```

If the product summary fails, these environment jobs are marked `failed` automatically before they start.

## Important Semantics

### Dependencies enforce ordering only

The backend does not inject dependency results into downstream plugin calls.

If a downstream action needs information produced by upstream jobs, it must read that information from the database or another persistent store.

### No batch workflow endpoint

There is currently no dedicated endpoint that creates the entire dependency graph in one call.

Clients create jobs step by step and connect them with `depends_on_job_ids` and a shared `workflow_id`.

### No retry model yet

There is currently no dedicated retry API for:

- individual failed jobs
- failed branches
- entire workflows

Clients that want retry behavior should create new jobs explicitly.

### Existing clients still work for simple jobs

If no dependency fields are provided, jobs behave as before:

- they are created immediately
- they are scheduled immediately
- they run without workflow coordination beyond their own generated workflow metadata

## Logging and Observability

### Job Logs

Plugin stdout and stderr are captured line-by-line and stored in `job_logs`.

This includes:

- `print()` output from plugins
- stack traces on errors
- command output emitted through `run_command()`

### Agent Events

Some jobs, especially `chat_message`, emit activity events that can be streamed via SSE.

### Summary vs Failure

Use these fields as follows:

- `summary`: short human-readable status text
- `failure`: detailed failure text or traceback
- `result`: structured success payload

## AskPass Integration

`doc_action` jobs may execute Git operations that require credentials.

When this happens:

- the job environment is configured with `GIT_ASKPASS`
- the askpass helper calls back into the API
- the UI can answer the prompt through askpass endpoints

See [docs/ASKPASS_SYSTEM.md](./ASKPASS_SYSTEM.md) for full details.

## Environment Variables

Relevant job-related environment variables:

| Variable | Purpose | Default |
|---|---|---|
| `DOP_JOBS_RETENTION_DAYS` | How long jobs are kept in DB | `7` |
| `DOP_JOBS_LIST_LIMIT` | Max jobs returned by list API / initial cache load | `100` |
| `DOP_WORKFLOW_MAX_PARALLEL` | Default max running jobs per workflow | `3` |
| `DOP_API_URL` | API URL used by askpass helper inside jobs | `http://localhost:10818` |

## Known Limitations

- dependencies are only supported when dependency job ids already exist
- there is no separate workflow table or workflow query API
- there is no batch graph creation endpoint
- there is no retry endpoint yet
- dependency results are not injected into downstream plugin actions
- cancellation support depends on the specific job type implementation

## File Map

- `api/jobs.py`: scheduler, execution, dependency logic, cancellation, serialization
- `api/db.py`: persistence for jobs, logs, and agent events
- `api/main.py`: public job endpoints
- `api/schemas.py`: request payload models
- `docs/ASKPASS_SYSTEM.md`: credential prompt flow for git operations inside jobs
