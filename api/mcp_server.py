from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from db import Database, DopDoc
from doc_refs import doc_name_from_content
from doc_refs import search_docs as _search_docs

SEARCH_LIMIT = 20


def create_mcp_server(database: Database) -> FastMCP:
    # Expose MCP at the mount root so main app can mount it at /mcp.
    mcp = FastMCP("DevOps Pass AI", streamable_http_path="/", stateless_http=True)

    @mcp.tool()
    def list_doc_types() -> dict[str, Any]:
        """List all resources types available in DevOps Pass AI, with their application IDs and
        document counts. Use this to discover what kinds of DevOps resources are stored before
        searching or fetching specific documents.
        There are some specifal doc types that are usefult for retreiving info about Products (sets of applications) and their environments.
        - dop_products: documents of this type represent Products, which are collections of related applications and resources
        - dop_env: documents of this type represent Environments, which are specific deployments or instances of a Product (e.g. staging, production) and reference the resources they contain.

        """
        return {"doc_types": database.list_distinct_doc_types()}

    @mcp.tool()
    def search_docs(
        requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Search DevOps resource documents using a batch of search requests.

        Args:
            requests: Array of search request objects. Each item can contain:
              - query: Free-text search terms matched against document content.
              - doc_type: Optional document type filter.
              - app_id: Optional application ID filter.

        Returns a plain array of unique lightweight document entries
        (app_id, doc_type, name, url, fact). Uniqueness is based on
        document ID in the database. Invalid request items are ignored.
        """
        unique_docs_by_id: dict[int, DopDoc] = {}

        for item in requests:
            if not isinstance(item, dict):
                continue

            query_raw = item.get("query")
            doc_type_raw = item.get("doc_type")
            app_id_raw = item.get("app_id")

            query = str(query_raw).strip() if isinstance(query_raw, str) else None
            doc_type = str(doc_type_raw).strip() if isinstance(doc_type_raw, str) else None
            app_id = str(app_id_raw).strip() if isinstance(app_id_raw, str) else None

            if query == "":
                query = None
            if doc_type == "":
                doc_type = None
            if app_id == "":
                app_id = None

            if query is None and doc_type is None and app_id is None:
                continue

            docs = _search_docs(database, query=query, doc_type=doc_type, app_id=app_id)[:SEARCH_LIMIT]
            for doc in docs:
                unique_docs_by_id[int(doc.id)] = doc

        results: list[dict[str, Any]] = []
        for doc_id in sorted(unique_docs_by_id.keys()):
            doc = unique_docs_by_id[doc_id]
            name = doc_name_from_content(doc.content)
            url_raw = doc.content.get("url")
            url = str(url_raw).strip() if url_raw else None
            entry: dict[str, Any] = {
                "app_id": doc.app_id,
                "doc_type": doc.doc_type,
                "name": name,
            }
            if url:
                entry["url"] = url
            if doc.fact:
                entry["fact"] = doc.fact
            results.append(entry)

        return results

    @mcp.tool()
    def get_doc(
        requests: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Retrieve the full content of specific DevOps resource documents using a batch of requests.

        Each request specifies the document to fetch by app_id, doc_type, name, and optionally url.
        The tool returns a plain array of full document entries (with content and fact_summary).
        Uniqueness is based on document ID — duplicate requests for the same document return
        only one result in the final array. Requests that do not match any document are silently
        skipped (not included in the output).

        Args:
            requests: Array of document retrieval request objects. Each item can contain:
              - app_id: Application ID that owns the document (required).
              - doc_type: Document type (required).
              - name: Document name to match (required).
              - url: Optional URL to disambiguate when multiple docs share the same name.

        Returns a plain array of full document entries, uniquely identified by internal
        document ID. Invalid request items are ignored.
        """
        unique_docs_by_id: dict[int, DopDoc] = {}

        def _serialize(doc: DopDoc) -> dict[str, Any]:
            d = doc.to_dict()
            d.pop("id", None)
            return d

        for item in requests:
            if not isinstance(item, dict):
                continue

            app_id_raw = item.get("app_id")
            doc_type_raw = item.get("doc_type")
            name_raw = item.get("name")
            url_raw = item.get("url")

            app_id = str(app_id_raw).strip() if isinstance(app_id_raw, str) else None
            doc_type = str(doc_type_raw).strip() if isinstance(doc_type_raw, str) else None
            name = str(name_raw).strip() if isinstance(name_raw, str) else None
            url = str(url_raw).strip() if isinstance(url_raw, str) else None

            if not app_id or not doc_type or not name:
                continue

            candidates = database.list_docs(doc_type=doc_type, app_id=app_id, include_facts=True)
            name_lower = name.lower()
            url_lower = url.lower() if url else None

            exact_url: list[DopDoc] = []
            by_name: list[DopDoc] = []

            for doc in candidates:
                doc_name = doc_name_from_content(doc.content).lower()
                doc_url_raw = doc.content.get("url")
                doc_url = str(doc_url_raw).strip().lower() if doc_url_raw else None

                if url_lower and doc_url and url_lower == doc_url:
                    exact_url.append(doc)
                    continue
                if doc_name and doc_name == name_lower:
                    by_name.append(doc)

            matched = exact_url if exact_url else by_name

            for doc in matched:
                unique_docs_by_id[int(doc.id)] = doc

        results: list[dict[str, Any]] = []
        for doc_id in sorted(unique_docs_by_id.keys()):
            doc = unique_docs_by_id[doc_id]
            results.append(_serialize(doc))

        return results

    return mcp
