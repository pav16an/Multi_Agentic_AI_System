import logging
from concurrent.futures import ThreadPoolExecutor
import re
from typing import Any, Dict, List
from pydantic import ValidationError

from agents import SummaryAgent, ActionAgent, RiskAgent
from vector_store import VectorStoreManager
from schemas import DocumentAnalysisOutput, DocumentChatOutput, ActionItem, RiskIssue
from llm_providers import LLMProvider

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DIRECT_ANALYSIS_CHAR_THRESHOLD = 6000
DIRECT_ANALYSIS_CONTEXT_CHARS = 1800
DIRECT_ANALYSIS_MAX_SEGMENTS = 3

class Orchestrator:
    def __init__(self, llm_provider: LLMProvider, model: str):
        self.llm_provider = llm_provider
        self.model = model
        self.vector_store: VectorStoreManager | None = None
        self.summary_agent = SummaryAgent(llm_provider, model)
        self.action_agent = ActionAgent(llm_provider, model)
        self.risk_agent = RiskAgent(llm_provider, model)

    def _get_vector_store(self) -> VectorStoreManager:
        if self.vector_store is None:
            self.vector_store = VectorStoreManager()
        return self.vector_store

    def _build_direct_context(self, text: str) -> list[str]:
        normalized = (text or "").strip()
        if not normalized:
            return []

        if len(normalized) <= DIRECT_ANALYSIS_CONTEXT_CHARS:
            return [normalized]

        segment_size = min(
            DIRECT_ANALYSIS_CONTEXT_CHARS,
            max(600, len(normalized) // DIRECT_ANALYSIS_MAX_SEGMENTS),
        )
        starts = [
            0,
            max((len(normalized) - segment_size) // 2, 0),
            max(len(normalized) - segment_size, 0),
        ]

        segments: list[str] = []
        seen = set()
        for start in starts:
            segment = normalized[start : start + segment_size].strip()
            if segment and segment not in seen:
                seen.add(segment)
                segments.append(segment)
        return segments or [normalized[:DIRECT_ANALYSIS_CONTEXT_CHARS]]
    
    def process_document(self, text: str, api_key: str) -> DocumentAnalysisOutput:
        if not text or not text.strip():
            raise ValueError("Document is empty after preprocessing.")

        logger.info("Processing...")

        stripped_text = text.strip()
        if len(stripped_text) <= DIRECT_ANALYSIS_CHAR_THRESHOLD:
            direct_context = self._build_direct_context(stripped_text)
            summary_context = direct_context
            action_context = direct_context
            risk_context = direct_context
        else:
            vector_store = self._get_vector_store()
            chunks = vector_store.create_chunks(stripped_text)
            if not chunks:
                raise ValueError("Document does not contain enough text to analyze.")

            vector_store.build_index()

            summary_context = self._collect_context(
                queries=[
                    "summary objective outcomes key decisions",
                    "main conclusions and commitments",
                    "deadlines deliverables owners risks",
                ],
                k_per_query=3,
                max_chunks=6,
            )
            action_context = self._collect_context(
                queries=[
                    "tasks action items owner deadline dependency",
                    "next steps responsibilities due dates",
                ],
                k_per_query=3,
                max_chunks=6,
            )
            risk_context = self._collect_context(
                queries=[
                    "risks blockers constraints dependencies",
                    "open questions assumptions missing information",
                    "uncertainty issues concerns",
                ],
                k_per_query=3,
                max_chunks=6,
            )
        
        results = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self.summary_agent.execute, 
                    summary_context, api_key): "summary",
                executor.submit(self.action_agent.execute, 
                    action_context, api_key): "actions",
                executor.submit(self.risk_agent.execute, 
                    risk_context, api_key): "risks"
            }
            
            for future in futures:
                try:
                    results[futures[future]] = future.result()
                except Exception as e:
                    logger.error(f"Error: {e}")
                    results[futures[future]] = None
        
        return self._aggregate(results)
    
    def _collect_context(
        self,
        *,
        queries: list[str],
        k_per_query: int = 4,
        max_chunks: int = 8,
    ) -> list[str]:
        vector_store = self._get_vector_store()
        seen = set()
        collected: list[str] = []

        for query in queries:
            for chunk in vector_store.retrieve_context(query, k=k_per_query):
                key = (chunk or "").strip()
                if key and key not in seen:
                    seen.add(key)
                    collected.append(key)
                if len(collected) >= max_chunks:
                    break
            if len(collected) >= max_chunks:
                break

        # Ensure document-level coverage for long docs.
        if vector_store.chunks:
            for edge_chunk in (vector_store.chunks[0], vector_store.chunks[-1]):
                key = (edge_chunk or "").strip()
                if key and key not in seen:
                    seen.add(key)
                    collected.append(key)
                if len(collected) >= max_chunks:
                    break

        if not collected:
            return vector_store.chunks[: min(max_chunks, len(vector_store.chunks))]
        return collected[:max_chunks]

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip()).lower()

    def _clean_summary(self, summary: str) -> str:
        cleaned = (summary or "").strip()
        if not cleaned:
            return "No summary available."
        return re.sub(r"\n{3,}", "\n\n", cleaned)

    def _clean_actions(self, raw_actions) -> list[ActionItem]:
        action_items = []
        seen = set()
        for item in raw_actions or []:
            try:
                action = ActionItem(**item)
            except ValidationError as exc:
                logger.warning("Skipping invalid action item %s: %s", item, exc)
                continue

            dedupe_key = (
                self._normalize_text(action.task),
                self._normalize_text(action.owner),
                self._normalize_text(action.deadline),
            )
            if not dedupe_key[0] or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            action_items.append(action)
        return action_items

    def _clean_risks(self, raw_risks) -> list[RiskIssue]:
        risks = []
        seen = set()
        for item in raw_risks or []:
            try:
                risk = RiskIssue(**item)
            except ValidationError as exc:
                logger.warning("Skipping invalid risk item %s: %s", item, exc)
                continue

            dedupe_key = (
                risk.type,
                self._normalize_text(risk.description),
            )
            if not dedupe_key[1] or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            risks.append(risk)

        order = {"Risk": 0, "Open Question": 1, "Assumption": 2, "Missing Info": 3}
        risks.sort(key=lambda item: (order.get(item.type, 99), item.description.lower()))
        return risks

    def _aggregate(self, results):
        summary = self._clean_summary(results.get("summary") or "")
        action_items = self._clean_actions(results.get("actions"))
        risks = self._clean_risks(results.get("risks"))
        
        return DocumentAnalysisOutput(
            summary=summary,
            action_items=action_items,
            risks_and_open_issues=risks
        )

    def answer_document_question(
        self,
        *,
        text: str,
        question: str,
        api_key: str,
        chat_history: List[Dict[str, Any]] | None = None,
    ) -> DocumentChatOutput:
        normalized_question = (question or "").strip()
        if not text or not text.strip():
            raise ValueError("Document is empty after preprocessing.")
        if not normalized_question:
            raise ValueError("Question is required for document chat.")

        vector_store = self._get_vector_store()
        chunks = vector_store.create_chunks(text)
        if not chunks:
            raise ValueError("Document does not contain enough text to answer questions.")

        vector_store.build_index()
        return self._answer_document_question_with_index(
            question=normalized_question,
            api_key=api_key,
            chat_history=chat_history,
        )

    def answer_document_question_from_store(
        self,
        *,
        question: str,
        api_key: str,
        chat_history: List[Dict[str, Any]] | None = None,
    ) -> DocumentChatOutput:
        normalized_question = (question or "").strip()
        if not normalized_question:
            raise ValueError("Question is required for document chat.")
        vector_store = self._get_vector_store()
        if not vector_store.chunks or vector_store.index is None:
            raise ValueError("Document chat context is not initialized.")

        return self._answer_document_question_with_index(
            question=normalized_question,
            api_key=api_key,
            chat_history=chat_history,
        )

    def _answer_document_question_with_index(
        self,
        *,
        question: str,
        api_key: str,
        chat_history: List[Dict[str, Any]] | None = None,
    ) -> DocumentChatOutput:
        context_chunks = self._collect_context(
            queries=[
                question,
                "main points decisions timeline owners",
                "risks assumptions open questions missing info",
            ],
            k_per_query=4,
            max_chunks=9,
        )

        context_block = "\n\n".join(
            [f"- {chunk}" for chunk in context_chunks]
        )
        history_lines: List[str] = []
        for item in (chat_history or [])[-8:]:
            role = str(item.get("role", "")).strip().lower()
            content = str(item.get("content", "")).strip()
            if role in {"user", "assistant"} and content:
                history_lines.append(f"{role.title()}: {content}")
        history_block = "\n".join(history_lines) if history_lines else "None"

        prompt = (
            "You are a document Q&A assistant.\n"
            "Answer the question using only the provided context chunks.\n"
            "If information is not in the context, explicitly say it is not found.\n"
            "Keep the answer concise and factual.\n"
            "Do not mention chunk labels or references like [Chunk 1] in the final answer.\n\n"
            f"Recent Conversation:\n{history_block}\n\n"
            f"Context:\n{context_block}\n\n"
            f"Question: {question}\n"
            "Answer:"
        )

        response = self.llm_provider.complete(
            prompt=prompt,
            model=self.model,
            api_key=api_key,
            temperature=0.0,
            max_tokens=700,
        )
        answer = (response or "").strip() or "I could not generate an answer from this document."

        source_chunks = []
        for chunk in context_chunks[:3]:
            snippet = chunk.strip()
            if len(snippet) > 220:
                snippet = snippet[:220].rstrip() + "..."
            source_chunks.append(snippet)

        return DocumentChatOutput(
            question=question,
            answer=answer,
            source_chunks=source_chunks,
        )
        
