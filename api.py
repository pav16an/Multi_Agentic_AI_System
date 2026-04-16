import json
import hashlib
import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Any, Deque, Dict
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from document_processor import DocumentProcessor
from llm_providers import (
    DEFAULT_MODELS,
    PROVIDER_MODEL_SUGGESTIONS,
    ProviderError,
    get_provider_adapter,
    normalize_provider,
    resolve_model,
)
from orchestrator import Orchestrator
from schemas import DocumentAnalysisOutput, DocumentChatOutput, TextToSQLOutput
from text_to_sql import DatabaseDataProcessor, TextToSQLService, StructuredDataProcessor
from vector_store import VectorStoreManager

try:
    from redis.asyncio import Redis
    from redis.exceptions import RedisError
except Exception:  # pragma: no cover - optional dependency fallback
    Redis = None

    class RedisError(Exception):
        pass


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx"}
ALLOWED_STRUCTURED_EXTENSIONS = StructuredDataProcessor.SUPPORTED_EXTENSIONS
MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "10"))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
RATE_LIMIT_PER_MIN = int(os.getenv("RATE_LIMIT_PER_MIN", "60"))
APP_AUTH_TOKEN = os.getenv("APP_AUTH_TOKEN", "").strip()
REDIS_URL = os.getenv("REDIS_URL", "").strip()
ENABLE_CLEANUP_ENDPOINT = os.getenv("ENABLE_CLEANUP_ENDPOINT", "false").strip().lower() in {
    "1",
    "true",
    "yes",
}
UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
_REQUEST_BUCKETS: Dict[str, Deque[float]] = defaultdict(deque)
_RATE_LIMIT_LOCK = Lock()
_REDIS_CLIENT = None
_DOC_CHAT_CACHE: Dict[str, Dict[str, Any]] = {}
_DOC_CHAT_CACHE_LOCK = Lock()
DOC_CHAT_CACHE_MAX_ITEMS = int(os.getenv("DOC_CHAT_CACHE_MAX_ITEMS", "30"))
DOC_CHAT_CACHE_TTL_SEC = int(os.getenv("DOC_CHAT_CACHE_TTL_SEC", "3600"))

raw_cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:8501,http://127.0.0.1:8501",
)
allowed_origins = [origin.strip() for origin in raw_cors_origins.split(",") if origin.strip()]

app = FastAPI(
    title="Multi-Agent Document Intelligence API",
    description="Analyze documents to extract summaries, action items, and risks.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


def _require_app_token(x_app_token: str = Header(default="", alias="X-App-Token")) -> None:
    if not APP_AUTH_TOKEN:
        return
    if (x_app_token or "").strip() != APP_AUTH_TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized request token.",
        )


def _use_redis_rate_limit() -> bool:
    return bool(REDIS_URL)


async def _get_redis_client():
    global _REDIS_CLIENT
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    if Redis is None:
        raise RuntimeError("Redis package is not installed; cannot use REDIS_URL mode.")

    _REDIS_CLIENT = Redis.from_url(REDIS_URL, decode_responses=True)
    return _REDIS_CLIENT


async def _enforce_rate_limit(request: Request, scope: str) -> None:
    if RATE_LIMIT_PER_MIN <= 0:
        return

    client_ip = (request.client.host if request.client else "") or "unknown"
    now = time.time()
    window_start = now - 60.0

    if _use_redis_rate_limit():
        try:
            redis_client = await _get_redis_client()
            redis_window = int(now // 60)
            redis_key = f"rate_limit:{scope}:{client_ip}:{redis_window}"
            count = await redis_client.incr(redis_key)
            if count == 1:
                await redis_client.expire(redis_key, 61)
            if count > RATE_LIMIT_PER_MIN:
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded. Retry in about a minute.",
                )
            return
        except (RedisError, RuntimeError) as exc:
            logger.warning(
                "Redis rate limit unavailable, falling back to in-memory limiter: %s", exc
            )

    key = f"{scope}:{client_ip}"
    with _RATE_LIMIT_LOCK:
        bucket = _REQUEST_BUCKETS[key]
        while bucket and bucket[0] < window_start:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_PER_MIN:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Retry in about a minute.",
            )
        bucket.append(now)


def _document_id_from_file(filename: str, file_bytes: bytes) -> str:
    digest = hashlib.sha256(file_bytes).hexdigest()[:16]
    return f"{filename}:{digest}"


def _cleanup_doc_chat_cache() -> None:
    now = time.time()
    with _DOC_CHAT_CACHE_LOCK:
        expired = [
            doc_id
            for doc_id, item in _DOC_CHAT_CACHE.items()
            if now - float(item.get("updated_at", 0)) > DOC_CHAT_CACHE_TTL_SEC
        ]
        for doc_id in expired:
            _DOC_CHAT_CACHE.pop(doc_id, None)

        if len(_DOC_CHAT_CACHE) <= DOC_CHAT_CACHE_MAX_ITEMS:
            return

        sorted_items = sorted(
            _DOC_CHAT_CACHE.items(),
            key=lambda kv: float(kv[1].get("updated_at", 0)),
        )
        overflow = len(_DOC_CHAT_CACHE) - DOC_CHAT_CACHE_MAX_ITEMS
        for idx in range(overflow):
            _DOC_CHAT_CACHE.pop(sorted_items[idx][0], None)


def _set_doc_chat_cache(doc_id: str, vector_store: VectorStoreManager) -> None:
    with _DOC_CHAT_CACHE_LOCK:
        _DOC_CHAT_CACHE[doc_id] = {
            "vector_store": vector_store,
            "updated_at": time.time(),
        }
    _cleanup_doc_chat_cache()


def _get_doc_chat_cache(doc_id: str) -> Dict[str, Any] | None:
    _cleanup_doc_chat_cache()
    with _DOC_CHAT_CACHE_LOCK:
        item = _DOC_CHAT_CACHE.get(doc_id)
        if not item:
            return None
        item["updated_at"] = time.time()
        return item


@app.get("/")
def read_root():
    return {
        "message": "Multi-Agent Document Intelligence API",
        "status": "running",
        "endpoints": {
            "GET /providers": "Supported providers and suggested models",
            "POST /analyze": "Upload and analyze a document",
            "POST /document-chat": "Ask follow-up questions on an uploaded document",
            "POST /text-to-sql": "Ask a question over structured data",
            "GET /health": "Check API health",
        },
    }


@app.get("/health")
def health_check():
    return {"status": "healthy", "max_file_size_mb": MAX_FILE_SIZE_MB}


@app.get("/providers")
def list_providers():
    return {
        "providers": list(DEFAULT_MODELS.keys()),
        "default_models": DEFAULT_MODELS,
        "suggested_models": PROVIDER_MODEL_SUGGESTIONS,
    }


@app.on_event("shutdown")
async def shutdown_redis_client() -> None:
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        return

    try:
        aclose = getattr(_REDIS_CLIENT, "aclose", None)
        if callable(aclose):
            await aclose()
        else:
            await _REDIS_CLIENT.close()
    except Exception:
        logger.exception("Failed to close Redis client cleanly.")
    finally:
        _REDIS_CLIENT = None


@app.post("/analyze", response_model=DocumentAnalysisOutput)
async def analyze_document(
    request: Request,
    file: UploadFile = File(...),
    provider: str = Form(...),
    model: str = Form(""),
    api_key: str = Form(...),
    _auth: None = Depends(_require_app_token),
):
    await _enforce_rate_limit(request, "analyze")

    if not (api_key or "").strip():
        raise HTTPException(status_code=400, detail="API key is required.")

    safe_filename = Path(file.filename or "").name
    file_ext = Path(safe_filename).suffix.lower()
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{file_ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(file_bytes) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {MAX_FILE_SIZE_MB} MB limit.",
        )

    stored_path = UPLOAD_DIR / f"{uuid4().hex}{file_ext}"

    try:
        normalized_provider = normalize_provider(provider)
        resolved_model = resolve_model(normalized_provider, model)
        llm_provider = get_provider_adapter(normalized_provider)

        with open(stored_path, "wb") as output_file:
            output_file.write(file_bytes)

        text = DocumentProcessor.load_document(stored_path)
        text = DocumentProcessor.preprocess(text)
        if not text:
            raise HTTPException(status_code=400, detail="Document has no readable text.")

        orchestrator = Orchestrator(llm_provider=llm_provider, model=resolved_model)
        return orchestrator.process_document(text=text, api_key=api_key.strip())

    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to analyze document.")
        raise HTTPException(
            status_code=500,
            detail="Analysis failed. Verify provider, model, API key, and input document.",
        ) from exc
    finally:
        if stored_path.exists():
            stored_path.unlink()
        await file.close()


@app.post("/document-chat", response_model=DocumentChatOutput)
async def chat_with_document(
    request: Request,
    file: UploadFile | None = File(None),
    question: str = Form(...),
    chat_history: str = Form(""),
    doc_id: str = Form(""),
    provider: str = Form(...),
    model: str = Form(""),
    api_key: str = Form(...),
    _auth: None = Depends(_require_app_token),
):
    await _enforce_rate_limit(request, "document_chat")

    if not (api_key or "").strip():
        raise HTTPException(status_code=400, detail="API key is required.")
    if not (question or "").strip():
        raise HTTPException(status_code=400, detail="Question is required.")
    normalized_doc_id = (doc_id or "").strip()
    has_file = bool(file and (file.filename or "").strip())
    stored_path: Path | None = None

    try:
        parsed_history = []
        if (chat_history or "").strip():
            try:
                payload = json.loads(chat_history)
                if isinstance(payload, list):
                    parsed_history = payload
            except json.JSONDecodeError:
                parsed_history = []

        normalized_provider = normalize_provider(provider)
        resolved_model = resolve_model(normalized_provider, model)
        llm_provider = get_provider_adapter(normalized_provider)
        orchestrator = Orchestrator(llm_provider=llm_provider, model=resolved_model)

        if has_file:
            safe_filename = Path(file.filename or "").name
            file_ext = Path(safe_filename).suffix.lower()
            if file_ext not in ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unsupported file type '{file_ext}'. Allowed: "
                        + ", ".join(sorted(ALLOWED_EXTENSIONS))
                    ),
                )

            file_bytes = await file.read()
            if not file_bytes:
                raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            if len(file_bytes) > MAX_FILE_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {MAX_FILE_SIZE_MB} MB limit.",
                )

            stored_path = UPLOAD_DIR / f"{uuid4().hex}{file_ext}"
            with open(stored_path, "wb") as output_file:
                output_file.write(file_bytes)

            text = DocumentProcessor.load_document(stored_path)
            text = DocumentProcessor.preprocess(text)
            if not text:
                raise HTTPException(status_code=400, detail="Document has no readable text.")

            vector_store = VectorStoreManager()
            chunks = vector_store.create_chunks(text)
            if not chunks:
                raise HTTPException(
                    status_code=400,
                    detail="Document does not contain enough text to answer questions.",
                )
            vector_store.build_index()

            resolved_doc_id = normalized_doc_id or _document_id_from_file(safe_filename, file_bytes)
            _set_doc_chat_cache(
                doc_id=resolved_doc_id,
                vector_store=vector_store,
            )
            orchestrator.vector_store = vector_store
        else:
            if not normalized_doc_id:
                raise HTTPException(
                    status_code=400,
                    detail="Provide either a document file or a known doc_id.",
                )
            cached = _get_doc_chat_cache(normalized_doc_id)
            if not cached:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Document chat context not found in cache for this doc_id. "
                        "Send the file once to initialize fast chat."
                    ),
                )
            orchestrator.vector_store = cached["vector_store"]

        return orchestrator.answer_document_question_from_store(
            question=question.strip(),
            api_key=api_key.strip(),
            chat_history=parsed_history,
        )

    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Document chat request failed.")
        raise HTTPException(
            status_code=500,
            detail="Document chat failed. Verify provider, model, API key, and input document.",
        ) from exc
    finally:
        if stored_path and stored_path.exists():
            stored_path.unlink()
        if file is not None:
            await file.close()


@app.post("/text-to-sql", response_model=TextToSQLOutput)
async def text_to_sql(
    request: Request,
    file: UploadFile | None = File(None),
    question: str = Form(...),
    provider: str = Form(...),
    model: str = Form(""),
    api_key: str = Form(...),
    db_connection_uri: str = Form(""),
    db_include_tables: str = Form(""),
    db_type: str = Form(""),
    db_host: str = Form(""),
    db_port: str = Form(""),
    db_name: str = Form(""),
    db_user: str = Form(""),
    db_password: str = Form(""),
    _auth: None = Depends(_require_app_token),
):
    await _enforce_rate_limit(request, "text_to_sql")

    if not (api_key or "").strip():
        raise HTTPException(status_code=400, detail="API key is required.")
    if not (question or "").strip():
        raise HTTPException(status_code=400, detail="Question is required.")

    has_file = bool(file and (file.filename or "").strip())
    has_db_uri = bool((db_connection_uri or "").strip())
    has_db_credentials = any(
        [
            (db_type or "").strip(),
            (db_host or "").strip(),
            (db_port or "").strip(),
            (db_name or "").strip(),
            (db_user or "").strip(),
            (db_password or "").strip(),
        ]
    )
    has_db = has_db_uri or has_db_credentials

    if has_file and has_db:
        raise HTTPException(
            status_code=400,
            detail="Provide either file upload or database connection details, not both.",
        )
    if not has_file and not has_db:
        raise HTTPException(
            status_code=400,
            detail="Provide a structured file upload or database connection details.",
        )

    stored_path: Path | None = None

    try:
        normalized_provider = normalize_provider(provider)
        resolved_model = resolve_model(normalized_provider, model)
        llm_provider = get_provider_adapter(normalized_provider)
        service = TextToSQLService(llm_provider=llm_provider, model=resolved_model)

        if has_file:
            safe_filename = Path(file.filename or "").name
            file_ext = Path(safe_filename).suffix.lower()
            if file_ext not in ALLOWED_STRUCTURED_EXTENSIONS:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Unsupported structured file type. Allowed: "
                        + ", ".join(sorted(ALLOWED_STRUCTURED_EXTENSIONS))
                    ),
                )

            file_bytes = await file.read()
            if not file_bytes:
                raise HTTPException(status_code=400, detail="Uploaded file is empty.")
            if len(file_bytes) > MAX_FILE_SIZE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {MAX_FILE_SIZE_MB} MB limit.",
                )

            stored_path = UPLOAD_DIR / f"{uuid4().hex}{file_ext}"
            with open(stored_path, "wb") as output_file:
                output_file.write(file_bytes)

            return service.answer_question(
                file_path=stored_path,
                question=question.strip(),
                api_key=api_key.strip(),
            )

        include_tables = DatabaseDataProcessor.parse_include_tables(db_include_tables)
        resolved_connection_uri = (db_connection_uri or "").strip()
        if not resolved_connection_uri:
            resolved_connection_uri = DatabaseDataProcessor.build_connection_uri(
                db_type=db_type,
                host=db_host,
                port=db_port,
                database=db_name,
                username=db_user,
                password=db_password,
            )

        return service.answer_question(
            connection_uri=resolved_connection_uri,
            include_tables=include_tables,
            question=question.strip(),
            api_key=api_key.strip(),
        )

    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Text-to-SQL request failed.")
        raise HTTPException(
            status_code=500,
            detail=(
                "Text-to-SQL failed. Verify provider, model, API key, and your "
                "file or database connection."
            ),
        ) from exc
    finally:
        if stored_path and stored_path.exists():
            stored_path.unlink()
        if file is not None:
            await file.close()


@app.delete("/cleanup")
def cleanup_uploads(
    _auth: None = Depends(_require_app_token),
):
    if not ENABLE_CLEANUP_ENDPOINT:
        raise HTTPException(
            status_code=403,
            detail="Cleanup endpoint is disabled in this environment.",
        )

    count = 0
    for upload in UPLOAD_DIR.glob("*"):
        if upload.is_file():
            upload.unlink()
            count += 1
    return {"message": f"Deleted {count} files"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
