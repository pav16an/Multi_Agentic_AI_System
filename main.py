import sys
import json
from pathlib import Path
from document_processor import DocumentProcessor
from orchestrator import Orchestrator

def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <document.txt>")
        return
    
    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"File not found: {file_path}")
        return
    
    print(f"Processing: {file_path.name}")
    
    text = DocumentProcessor.load_document(file_path)
    text = DocumentProcessor.preprocess(text)
    
    orchestrator = Orchestrator()
    result = orchestrator.process_document(text)
    
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    print(json.dumps(result.model_dump(), indent=2))

if __name__ == "__main__":
    main()