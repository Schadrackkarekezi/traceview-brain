"""
Pydantic models define the request/response shapes for the API.

FastAPI uses these for three things at once: validating incoming JSON,
serializing outgoing JSON, and generating the OpenAPI docs at /docs -
one class definition drives all three.
"""

from typing import Any, Optional, Union
from pydantic import BaseModel


class ColumnInfo(BaseModel):
    name: str
    type: str


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo]
    kind: Optional[str] = None  # "dimension" | "fact" - from the ontology, not guessed


class SchemaResponse(BaseModel):
    tables: list[TableInfo]


class GraphNode(BaseModel):
    id: str
    kind: str
    description: str


class GraphEdge(BaseModel):
    source: str
    target: str
    label: str
    via: str


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class QueryRequest(BaseModel):
    sql: str


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    execution_time_ms: float
    truncated: bool


class GlossaryHit(BaseModel):
    term: str
    definition: str
    score: float


class UnstructuredSource(BaseModel):
    chunk_id: Union[int, str]  # pgvector's integer row id, or HydraDB's chunk uuid
    source_type: str  # "account_note" | "enablement_content"
    source_id: Optional[int] = None
    account_id: Optional[int] = None
    title: Optional[str] = None  # HydraDB sources only
    text: str
    score: float


class Memory(BaseModel):
    text: str
    score: Optional[float] = None
    id: Optional[str] = None
    timestamp: Optional[str] = None
    origin: Optional[str] = None  # "conversation" (inferred from a turn) | "explicit" (typed by the user)


class GraphPath(BaseModel):
    path: str
    score: Optional[float] = None
    context: Optional[str] = None  # the sentence(s) the relation was declared with or extracted from


class AskRequest(BaseModel):
    question: str
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None  # who's asking - scopes long-term memory; omitted means no memory


class AskResponse(BaseModel):
    response_type: str  # "sql" | "chat" | "unstructured" | "hybrid"
    question: str
    message: Optional[str] = None
    explanation: Optional[str] = None  # sql mode only - a short plain-language note under the table
    generated_sql: Optional[str] = None
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    attempts: int
    verified: bool
    retrieved_context: list[GlossaryHit]
    retrieved_sources: list[UnstructuredSource] = []
    trace: list[dict[str, Any]]
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    conversation_id: str
    request_id: str
    # The company-brain layer - all empty/None when HydraDB isn't configured.
    memories: list[Memory] = []  # recalled about this user for this question
    graph_paths: list[GraphPath] = []  # entity relationships HydraDB's graph found relevant
    retrieval_backend: Optional[str] = None  # "hydradb" | "pgvector" - which store really answered
    hydra_request_id: Optional[str] = None  # the HydraDB query to attach feedback to
    remembered: Optional[str] = None  # a fact this turn revealed about the user, now saved to memory


class UploadNoteRequest(BaseModel):
    account_id: int
    content: str
    author_role: str = "CSM"


class UploadEnablementRequest(BaseModel):
    title: str
    category: str
    content: str


class UploadResponse(BaseModel):
    id: int
    chunks_created: int
    chunks_embedded: int
    hydra_indexed: Optional[bool] = None  # None when HydraDB isn't configured


class MemoryRequest(BaseModel):
    user_id: str
    text: str


class MemoriesResponse(BaseModel):
    user_id: str
    memories: list[Memory]


class FeedbackRequest(BaseModel):
    hydra_request_id: str
    rating: str  # "positive" | "negative"
    comment: Optional[str] = None


class LogEntry(BaseModel):
    request_id: Optional[str] = None  # absent on log lines written before this field existed
    timestamp: str
    question: str
    success: bool
    total_latency_ms: float
    response_type: Optional[str] = None
    generated_sql: Optional[str] = None
    verified: Optional[bool] = None
    attempts: Optional[int] = None
    row_count: Optional[int] = None
    retrieved_terms: list[str] = []
    estimated_cost_usd: Optional[float] = None
    error: Optional[str] = None
    trace: list[dict[str, Any]] = []


class LogsResponse(BaseModel):
    entries: list[LogEntry]
