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

class Orchestrator:
    def __init__(self, llm_provider: LLMProvider, model: str):
        self.llm_provider = llm_provider
        self.model = model
        self.vector_store = VectorStoreManager()
        self.summary_agent = SummaryAgent(llm_provider, model)
        self.action_agent = ActionAgent(llm_provider, model)
        self.risk_agent = RiskAgent(llm_provider, model)
    
    def process_document(self, text: str, api_key: str) -> DocumentAnalysisOutput:
        if not text or not text.strip():
            raise ValueError("Document is empty after preprocessing.")

        logger.info("Processing...")
        
        chunks = self.vector_store.create_chunks(text)
        if not chunks:
            raise ValueError("Document does not contain enough text to analyze.")

        self.vector_store.build_index()

        summary_context = self._collect_context(
            queries=[
                "summary objective outcomes key decisions",
                "main conclusions and commitments",
                "deadlines deliverables owners risks",
            ],
            k_per_query=4,
            max_chunks=8,
        )
        action_context = self._collect_context(
            queries=[
                "tasks action items owner deadline dependency",
                "next steps responsibilities due dates",
            ],
            k_per_query=4,
            max_chunks=8,
        )
        risk_context = self._collect_context(
            queries=[
                "risks blockers constraints dependencies",
                "open questions assumptions missing information",
                "uncertainty issues concerns",
            ],
            k_per_query=4,
            max_chunks=8,
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
        seen = set()
        collected: list[str] = []

        for query in queries:
            for chunk in self.vector_store.retrieve_context(query, k=k_per_query):
                key = (chunk or "").strip()
                if key and key not in seen:
                    seen.add(key)
                    collected.append(key)
                if len(collected) >= max_chunks:
                    break
            if len(collected) >= max_chunks:
                break

        # Ensure document-level coverage for long docs.
        if self.vector_store.chunks:
            for edge_chunk in (self.vector_store.chunks[0], self.vector_store.chunks[-1]):
                key = (edge_chunk or "").strip()
                if key and key not in seen:
                    seen.add(key)
                    collected.append(key)
                if len(collected) >= max_chunks:
                    break

        if not collected:
            return self.vector_store.chunks[: min(max_chunks, len(self.vector_store.chunks))]
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

        chunks = self.vector_store.create_chunks(text)
        if not chunks:
            raise ValueError("Document does not contain enough text to answer questions.")

        self.vector_store.build_index()
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
        if not self.vector_store.chunks or self.vector_store.index is None:
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
        
