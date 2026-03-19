from typing import Any

from pydantic import BaseModel, Field


class DocCreate(BaseModel):
    app_id: str | None = None
    doc_type: str
    content: dict[str, Any]


class ApplicationCreate(BaseModel):
    plugin_key: str
    app_id: str
    settings: dict[str, Any] = Field(default_factory=dict)


class ApplicationUpdate(BaseModel):
    content: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    description: str | None = None
    url: str | None = None


class ApplicationTest(BaseModel):
    plugin_key: str
    app_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class ProductResource(BaseModel):
    app_id: str
    doc_type: str
    name: str
    url: str | None = None


class ChatDocReference(BaseModel):
    app_id: str
    doc_type: str
    name: str
    url: str | None = None


class ProductCreate(BaseModel):
    product_id: str
    name: str
    prompt: str | None = None
    description: str | None = None
    icon: str | None = None
    url: str | None = None
    resources: list[ProductResource] = Field(default_factory=list)


class ProductUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    description: str | None = None
    icon: str | None = None
    url: str | None = None
    resources: list[ProductResource] | None = None


class SearchQuery(BaseModel):
    q: str | None = None
    doc_type: str | None = None
    app_id: str | None = None


class DocsRefreshJobCreate(BaseModel):
    app_doc_id: int | None = None
    app_id: str | None = None
    doc_type: str
    depends_on_job_ids: list[str] = Field(default_factory=list)
    workflow_id: str | None = None
    max_parallel: int | None = None


class DocActionJobCreate(BaseModel):
    doc_id: int
    action_name: str
    depends_on_job_ids: list[str] = Field(default_factory=list)
    workflow_id: str | None = None
    max_parallel: int | None = None


class AskPassRequest(BaseModel):
    job_id: str
    prompt: str


class AskPassResponse(BaseModel):
    password: str
    save: bool = False


class ChatAgentCreate(BaseModel):
    name: str
    title: str | None = None
    prompt: str
    description: str | None = None
    model: str | None = None


class ChatAgentUpdate(BaseModel):
    name: str | None = None
    title: str | None = None
    prompt: str | None = None
    description: str | None = None
    model: str | None = None


class ChatThreadCreate(BaseModel):
    name: str
    attached_docs: list[ChatDocReference] = Field(default_factory=list)


class ChatThreadUpdate(BaseModel):
    name: str | None = None
    attached_docs: list[ChatDocReference] | None = None


class ChatMessageCreate(BaseModel):
    text: str
    agent_mentions: list[str] = Field(default_factory=list)
    doc_mentions: list[ChatDocReference] = Field(default_factory=list)
