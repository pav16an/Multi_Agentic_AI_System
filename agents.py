import json
import logging
import re
from typing import List

from llm_providers import LLMProvider

logger = logging.getLogger(__name__)


def _strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[len("```json") :]
    if cleaned.startswith("```"):
        cleaned = cleaned[len("```") :]
    if cleaned.endswith("```"):
        cleaned = cleaned[: -len("```")]
    return cleaned.strip()


def _extract_json_array(text: str) -> list:
    cleaned = _strip_json_fence(text)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\[[\s\S]*\]", cleaned)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            return []
    return []


def _normalize_summary_text(text: str) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
    if not cleaned:
        return "No summary available."

    required_sections = ("Overview:", "Key Points:", "Decisions:", "Next Steps:")
    if all(section.lower() in cleaned.lower() for section in required_sections):
        return cleaned

    return (
        "Overview:\n"
        f"{cleaned}\n\n"
        "Key Points:\n- Not explicit in document.\n\n"
        "Decisions:\n- Not explicit in document.\n\n"
        "Next Steps:\n- Not explicit in document."
    )


def _normalize_risk_type(value: str) -> str:
    normalized = (value or "").strip().lower()
    mapping = {
        "risk": "Risk",
        "open question": "Open Question",
        "question": "Open Question",
        "assumption": "Assumption",
        "missing info": "Missing Info",
        "missing information": "Missing Info",
    }
    return mapping.get(normalized, "Risk")


class BaseAgent:
    def __init__(
        self,
        name: str,
        role_prompt: str,
        llm_provider: LLMProvider,
        model: str,
        *,
        max_tokens: int = 1000,
    ):
        self.name = name
        self.role_prompt = role_prompt
        self.llm_provider = llm_provider
        self.model = model
        self.max_tokens = max_tokens
    
    def execute(self, context: List[str], api_key: str):
        context_text = "\n\n".join(
            [f"[Chunk {i+1}]\n{chunk}" for i, chunk in enumerate(context)]
        ) or "No relevant context found."
        prompt = self.role_prompt.format(context=context_text)
        
        response = self.llm_provider.complete(
            prompt=prompt,
            model=self.model,
            api_key=api_key,
            temperature=0,
            max_tokens=self.max_tokens,
        )
        
        return self._parse_response(response)
    
    def _parse_response(self, response: str):
        return response

class SummaryAgent(BaseAgent):
    def __init__(self, llm_provider: LLMProvider, model: str):
        prompt = """You are a precise business analyst.
Create a clear summary using only the provided context.

Return plain text in exactly this structure:
Overview:
<2-3 sentences with purpose + outcome>

Key Points:
- <fact/decision 1>
- <fact/decision 2>
- <fact/decision 3>

Decisions:
- <decision 1 or "Not explicit in document.">

Next Steps:
- <next step 1 or "Not explicit in document.">

Rules:
- Be specific and avoid vague language.
- Do not invent information not present in context.
- Keep total length under 160 words.

{context}

Summary:"""
        super().__init__("summary", prompt, llm_provider, model, max_tokens=320)
    
    def _parse_response(self, response: str):
        return _normalize_summary_text(response)

class ActionAgent(BaseAgent):
    def __init__(self, llm_provider: LLMProvider, model: str):
        prompt = """Extract all explicit action items and tasks from the context below.
Return a JSON array only.

{context}

For each task, return:
- task: description of the task
- owner: person responsible (or "Not Specified")
- dependency: what must be done first (or "None")
- deadline: due date (or "Not Specified")

Rules:
- Include only tasks that are supported by the context.
- Keep each task concise and actionable.
- Remove duplicates.

Return ONLY the JSON array, nothing else.
Format: [{{"task":"...", "owner":"...", "dependency":"...", "deadline":"..."}}]

JSON:"""
        super().__init__("action", prompt, llm_provider, model, max_tokens=650)
    
    def _parse_response(self, response: str):
        parsed = _extract_json_array(response)
        if not parsed:
            logger.warning("Failed parsing action response JSON.")
            return []
        sanitized = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            task = str(item.get("task", "")).strip()
            if not task:
                continue
            sanitized.append(
                {
                    "task": task,
                    "owner": str(item.get("owner", "Not Specified")).strip() or "Not Specified",
                    "dependency": str(item.get("dependency", "None")).strip() or "None",
                    "deadline": str(item.get("deadline", "Not Specified")).strip()
                    or "Not Specified",
                }
            )
        return sanitized

class RiskAgent(BaseAgent):
    def __init__(self, llm_provider: LLMProvider, model: str):
        prompt = """Identify risks, open questions, assumptions, and missing information.
Return a JSON array only.

{context}

For each item, return:
- type: one of "Risk", "Open Question", "Assumption", or "Missing Info"
- description: use this exact pattern:
  "Issue: <what is uncertain/problematic> | Why it matters: <impact> | Evidence: <quote or reference from context, or Not explicit> | Recommended action: <next best step>"

Rules:
- Include only high-confidence items supported by context.
- Remove duplicates and vague statements.
- Prioritize material business/project impact.
- Max 15 items.

Return ONLY the JSON array, nothing else.
Format: [{{"type":"Risk", "description":"..."}}]

JSON:"""
        super().__init__("risk", prompt, llm_provider, model, max_tokens=750)
    
    def _parse_response(self, response: str):
        parsed = _extract_json_array(response)
        if not parsed:
            logger.warning("Failed parsing risk response JSON.")
            return []

        sanitized = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            description = str(item.get("description", "")).strip()
            if not description:
                continue
            if "Issue:" not in description:
                description = (
                    f"Issue: {description} | Why it matters: Not explicit | "
                    "Evidence: Not explicit | Recommended action: Clarify with stakeholders."
                )
            sanitized.append(
                {
                    "type": _normalize_risk_type(str(item.get("type", "Risk"))),
                    "description": description,
                }
            )
        return sanitized
