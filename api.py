from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from pathlib import Path
import shutil
from document_processor import DocumentProcessor
from orchestrator import Orchestrator
from schemas import DocumentAnalysisOutput

app = FastAPI(
    title="Multi-Agent Document Intelligence API",
    description="Analyze documents using AI agents to extract summaries, action items, and risks",
    version="1.0.0"
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create uploads directory
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

# Initialize orchestrator
orchestrator = Orchestrator()

@app.get("/")
def read_root():
    return {
        "message": "Multi-Agent Document Intelligence API",
        "status": "running",
        "endpoints": {
            "POST /analyze": "Upload and analyze a document",
            "GET /health": "Check API health"
        }
    }

@app.get("/health")
def health_check():
    return {"status": "healthy"}

@app.post("/analyze", response_model=DocumentAnalysisOutput)
async def analyze_document(file: UploadFile = File(...)):
    """
    Upload a document (TXT, PDF, DOCX) and get AI analysis
    
    Returns:
    - summary: Key points from the document
    - action_items: Tasks with owners and deadlines
    - risks_and_open_issues: Risks, questions, and blockers
    """
    
    # Validate file type
    allowed_extensions = ['.txt', '.pdf', '.docx', '.doc']
    file_ext = Path(file.filename).suffix.lower()
    
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"File type {file_ext} not supported. Allowed: {', '.join(allowed_extensions)}"
        )
    
    # Save uploaded file
    file_path = UPLOAD_DIR / file.filename
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        
        # Process document
        text = DocumentProcessor.load_document(file_path)
        text = DocumentProcessor.preprocess(text)
        
        # Analyze with agents
        result = orchestrator.process_document(text)
        
        # Clean up
        file_path.unlink()
        
        return result
        
    except Exception as e:
        # Clean up on error
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/cleanup")
def cleanup_uploads():
    """Delete all uploaded files"""
    count = 0
    for file in UPLOAD_DIR.glob("*"):
        if file.is_file():
            file.unlink()
            count += 1
    return {"message": f"Deleted {count} files"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
