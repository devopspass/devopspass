DevOps Pass AI API
==================

FastAPI backend for DevOps Pass AI.

Features
--------

- SQLite storage for docs (`.data/dop.sqlite3` by default)
- Plugin-driven application definitions from `plugins/*/app.yaml`
- User-added applications stored as docs with `doc_type = dop_app`
- Document search with token matching in JSON content

Endpoints
---------

- `GET /status`
- `GET /api/plugin-apps`
- `GET /api/applications`
- `POST /api/applications`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs/docs-refresh`
- `GET /api/docs?q=chef%20base%20cookbook&doc_type=gitlab_repos`
- `POST /api/docs`
