import json
import os
from typing import List
from groq import Groq

# Load API key from environment variable (recommended) or use default
api_key = os.getenv("GROQ_API_KEY", "gsk_YOUR_API_KEY_HERE")
client = Groq(api_key=api_key)

class BaseAgent:
    def __init__(self, name: str, role_prompt: str):
        self.name = name
        self.role_prompt = role_prompt
        self.model = "llama-3.3-70b-versatile"  # Updated model
    
    def execute(self, context: List[str]):
        context_text = "\n\n".join([f"[Chunk {i+1}]\n{chunk}" for i, chunk in enumerate(context)])
        prompt = self.role_prompt.format(context=context_text)
        
        response = client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1000
        )
        
        return self._parse_response(response.choices[0].message.content)
    
    def _parse_response(self, response: str):
        return response

class SummaryAgent(BaseAgent):
    def __init__(self):
        prompt = """Summarize in 3-5 sentences:

{context}

Summary:"""
        super().__init__("summary", prompt)
    
    def _parse_response(self, response: str):
        return response.strip()

class ActionAgent(BaseAgent):
    def __init__(self):
        prompt = """Extract ALL action items and tasks from the text below. Return as a JSON array.

{context}

For each task found, create an object with:
- task: description of the task
- owner: person responsible (or "Not Specified")
- dependency: what must be done first (or "None")
- deadline: due date (or "Not Specified")

Return ONLY the JSON array, nothing else.
Format: [{{"task":"...", "owner":"...", "dependency":"...", "deadline":"..."}}]

JSON:"""
        super().__init__("action", prompt)
    
    def _parse_response(self, response: str):
        response = response.strip().replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(response)
        except:
            return []

class RiskAgent(BaseAgent):
    def __init__(self):
        prompt = """Identify ALL risks, open questions, assumptions, and missing information from the text below.

{context}

For each issue found, create an object with:
- type: one of "Risk", "Open Question", "Assumption", or "Missing Info"
- description: clear description of the issue

Return ONLY the JSON array, nothing else.
Format: [{{"type":"Risk", "description":"..."}}]

JSON:"""
        super().__init__("risk", prompt)
    
    def _parse_response(self, response: str):
        response = response.strip().replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(response)
        except:
            return []