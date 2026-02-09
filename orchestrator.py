import logging
from concurrent.futures import ThreadPoolExecutor
from agents import SummaryAgent, ActionAgent, RiskAgent
from vector_store import VectorStoreManager
from schemas import DocumentAnalysisOutput, ActionItem, RiskIssue

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Orchestrator:
    def __init__(self):
        self.vector_store = VectorStoreManager()
        self.summary_agent = SummaryAgent()
        self.action_agent = ActionAgent()
        self.risk_agent = RiskAgent()
    
    def process_document(self, text):
        logger.info("Processing...")
        
        self.vector_store.create_chunks(text)
        self.vector_store.build_index()
        
        results = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(self.summary_agent.execute, 
                    self.vector_store.retrieve_context("summary", k=3)): "summary",
                executor.submit(self.action_agent.execute, 
                    self.vector_store.retrieve_context("tasks actions", k=3)): "actions",
                executor.submit(self.risk_agent.execute, 
                    self.vector_store.retrieve_context("risks questions", k=3)): "risks"
            }
            
            for future in futures:
                try:
                    results[futures[future]] = future.result()
                except Exception as e:
                    logger.error(f"Error: {e}")
                    results[futures[future]] = None
        
        return self._aggregate(results)
    
    def _aggregate(self, results):
        summary = results.get("summary", "No summary")
        
        action_items = []
        if results.get("actions"):
            for item in results["actions"]:
                try:
                    action_items.append(ActionItem(**item))
                except:
                    pass
        
        risks = []
        if results.get("risks"):
            for item in results["risks"]:
                try:
                    risks.append(RiskIssue(**item))
                except:
                    pass
        
        return DocumentAnalysisOutput(
            summary=summary,
            action_items=action_items,
            risks_and_open_issues=risks
        )
        