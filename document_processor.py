import re
from pathlib import Path
import PyPDF2
import docx

class DocumentProcessor:
    @staticmethod
    def load_document(file_path) -> str:
        file_path = Path(file_path)
        suffix = file_path.suffix.lower()
        
        if suffix == '.txt':
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                return f.read()
        if suffix == '.pdf':
            text = []
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text.append(page.extract_text() or "")
            return '\n'.join(text)
        if suffix == '.docx':
            doc = docx.Document(file_path)
            return '\n'.join([para.text for para in doc.paragraphs])

        raise ValueError(
            f"Unsupported file type '{suffix}'. Supported types: .txt, .pdf, .docx"
        )
    
    @staticmethod
    def preprocess(text: str) -> str:
        text = (text or "").replace("\x00", " ")
        text = re.sub(r'\s+', ' ', text)
        return text.strip()
