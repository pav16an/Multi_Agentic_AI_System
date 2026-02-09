from typing import List
import numpy as np
import faiss
from langchain.text_splitter import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

class VectorStoreManager:
    def __init__(self):
        self.embeddings = SentenceTransformer('all-MiniLM-L6-v2')
        self.chunks = []
        self.index = None
        
    def create_chunks(self, text):
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=400,
            chunk_overlap=40,
            separators=["\n\n", "\n", ". ", " "]
        )
        self.chunks = splitter.split_text(text)
        return self.chunks
    
    def build_index(self):
        chunk_embeddings = self.embeddings.encode(self.chunks)
        embeddings_array = np.array(chunk_embeddings).astype('float32')
        
        dimension = embeddings_array.shape[1]
        self.index = faiss.IndexFlatL2(dimension)
        self.index.add(embeddings_array)
    
    def retrieve_context(self, query, k=3):
        query_embedding = np.array([self.embeddings.encode(query)]).astype('float32')
        distances, indices = self.index.search(query_embedding, min(k, len(self.chunks)))
        return [self.chunks[i] for i in indices[0]]