import re
from pathlib import Path
import PyPDF2
import docx

class DocumentProcessor:
    @staticmethod
    def load_document(file_path):
        file_path = Path(file_path)
        
        if file_path.suffix.lower() == '.txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        elif file_path.suffix.lower() == '.pdf':
            text = []
            with open(file_path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    text.append(page.extract_text())
            return '\n'.join(text)
        elif file_path.suffix.lower() in ['.docx', '.doc']:
            doc = docx.Document(file_path)
            return '\n'.join([para.text for para in doc.paragraphs])
    
    @staticmethod
    def preprocess(text):
        text = re.sub(r'\s+', ' ', text)
        return text.strip()