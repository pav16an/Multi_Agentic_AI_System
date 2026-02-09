from pydantic import BaseModel, Field
from typing import List, Literal

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