# DevOps Pass AI — MCP Server

DevOps Pass AI exposes a [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server endpoint so that AI agents such as GitHub Copilot can query DevOps resources stored in the application.

## Endpoint

**Transport:** Streamable HTTP
**URL:** `POST /mcp`

No authentication is required at this stage (same as the rest of the API). Token-based auth will be added in a future release.

## Connecting from GitHub Copilot (VS Code)

Add the following to your MCP configuration (`.vscode/mcp.json` or user settings):

```json
{
  "servers": {
    "devops-pass-ai": {
      "type": "http",
      "url": "http://localhost:10818/mcp"
    }
  }
}
```

---

## Tools

### `list_doc_types`

Lists all distinct document types stored in DevOps Pass AI, with their application IDs and document counts.

**Input:** none

**Output example:**
```json
{
  "doc_types": [
    {"doc_type": "gitlab_repos", "app_id": "my-gitlab", "count": 42},
    {"doc_type": "github_repos", "app_id": "my-github", "count": 17}
  ]
}
```

---

### `search_docs`

Batch full-text search across stored DevOps resource documents. Accepts an array of search requests and returns a single deduplicated array of lightweight entries — `app_id`, `doc_type`, `name`, `url`, and full `fact` text when available. Use `get_doc` to fetch the full content of a specific result.

**Input:**

`search_docs` accepts one parameter:

| Field      | Type                  | Required | Description |
|------------|-----------------------|----------|-------------|
| `requests` | array of search items | yes      | Batch of search requests |

Each search item has the same fields as before:

| Field      | Type   | Required | Description |
|------------|--------|----------|-------------|
| `query`    | string | no       | Space-separated search terms matched against document content |
| `doc_type` | string | no       | Filter to a specific document type |
| `app_id`   | string | no       | Filter to a specific integration instance |

At most **20** results are taken per request item. Final output is deduplicated by internal document ID.

**Output example:**
```json
[
  {
    "app_id": "my-gitlab",
    "doc_type": "gitlab_repos",
    "name": "backend-service",
    "url": "https://gitlab.example.com/org/backend-service",
    "fact": "Go microservice handling authentication and authorization for user-facing APIs."
  }
]
```

**Input example:**
```json
{
  "requests": [
    {"query": "flexcube db3", "doc_type": "jira_issues", "app_id": "jira"},
    {"query": "flexcube db3", "doc_type": "confluence_pages", "app_id": "confluence"},
    {"query": "db3"}
  ]
}
```

---

### `get_doc`

Batch retrieval of full document contents using a batch of requests. Returns the full document content dict and AI-generated fact summary for each matched document. Requests that do not match any document are silently skipped.

**Input:**

`get_doc` accepts one parameter:

| Field      | Type                    | Required | Description |
|------------|-------------------------|----------|-------------|
| `requests` | array of retrieval items | yes      | Batch of document retrieval requests |

Each retrieval item has the same fields as before:

| Field      | Type   | Required | Description                                                                 |
|------------|--------|----------|-----------------------------------------------------------------------------|
| `app_id`   | string | yes      | Application ID that owns the document                                       |
| `doc_type` | string | yes      | Document type (e.g. `gitlab_repos`, `github_repos`)                         |
| `name`     | string | yes      | Document name, matched against `name`/`title`/`path` fields in the content  |
| `url`      | string | no       | Optional URL to disambiguate when multiple documents share the same name     |

**Output example:**
```json
[
  {
    "app_id": "my-gitlab",
    "doc_type": "gitlab_repos",
    "content": { "path": "org/backend-service", "url": "https://...", ... },
    "fact_summary": "Go microservice handling authentication and authorization…"
  }
]
```

**Input example:**
```json
{
  "requests": [
    {"app_id": "my-gitlab", "doc_type": "gitlab_repos", "name": "backend-service"},
    {"app_id": "my-github", "doc_type": "github_repos", "name": "sdk-repo", "url": "https://github.com/org/sdk-repo"},
    {"app_id": "confluence", "doc_type": "confluence_pages", "name": "API Documentation"}
  ]
}
```

---

## Typical Copilot Workflow

1. Call `list_doc_types` to discover what resource types are available and which apps have data.
2. Call `search_docs` with one or more search request items to narrow down the documents of interest.
3. Call `get_doc` with a batch of retrieval requests (app_id, doc_type, name, optional url) to fetch full details and AI-generated fact summaries for each matched document.

## Implementation Notes

- The MCP server is implemented in `api/mcp_server.py` using the `mcp` Python SDK (`FastMCP`).
- It is mounted inside the main FastAPI application (`api/main.py`) at `/mcp`.
- Tools share the same `Database` instance as the rest of the API — no separate process or connection pool.
- The `search_docs` tool reuses the existing `search_docs` helper from `api/doc_refs.py`.
- The `fact` field on a document contains a free-text AI-generated summary written via the products/facts workflow.
