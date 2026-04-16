from pydantic import BaseModel, Field
from typing import Any, Dict, List, Literal

class ActionItem(BaseModel):
    task: str
    owner: str = "Not Specified"
    dependency: str = "None"
    deadline: str = "Not Specified"

class RiskIssue(BaseModel):
    type: Literal["Risk", "Open Question", "Assumption", "Missing Info"]
    description: str

class DocumentAnalysisOutput(BaseModel):
    summary: str
    action_items: List[ActionItem] = Field(default_factory=list)
    risks_and_open_issues: List[RiskIssue] = Field(default_factory=list)


class DocumentChatOutput(BaseModel):
    question: str
    answer: str
    source_chunks: List[str] = Field(default_factory=list)


class TextToSQLOutput(BaseModel):
    question: str
    sql: str
    answer: str
    columns: List[str] = Field(default_factory=list)
    rows: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    column_mapping: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
    source_type: Literal["file", "database"] = "file"
    source_name: str = ""
    tables: List[str] = Field(default_factory=list)
