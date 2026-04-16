from typing import List
import numpy as np
import faiss
from langchain.text_splitter import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

_EMBEDDING_MODEL = None


def _get_embedding_model():
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        _EMBEDDING_MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    return _EMBEDDING_MODEL


class VectorStoreManager:
    def __init__(self):
        self.embeddings = _get_embedding_model()
        self.chunks = []
        self.index = None
        
    def create_chunks(self, text: str) -> List[str]:
        text = (text or "").strip()
        if not text:
            self.chunks = []
            return self.chunks

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=400,
            chunk_overlap=40,
            separators=["\n\n", "\n", ". ", " "]
        )
        self.chunks = splitter.split_text(text)
        return self.chunks
    
    def build_index(self):
        if not self.chunks:
            raise ValueError("Cannot build vector index because document has no chunks.")

        chunk_embeddings = self.embeddings.encode(self.chunks)
        embeddings_array = np.array(chunk_embeddings).astype('float32')
        
        dimension = embeddings_array.shape[1]
        self.index = faiss.IndexFlatL2(dimension)
        self.index.add(embeddings_array)
    
    def retrieve_context(self, query: str, k: int = 3) -> List[str]:
        if not self.chunks:
            return []
        if self.index is None:
            return self.chunks[: min(k, len(self.chunks))]

        query_embedding = np.array([self.embeddings.encode(query)]).astype('float32')
        _, indices = self.index.search(query_embedding, min(k, len(self.chunks)))
        return [self.chunks[i] for i in indices[0]]
