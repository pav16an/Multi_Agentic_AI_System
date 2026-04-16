# Multi-Agent Document Intelligence System

Analyze TXT, PDF, and DOCX files with three specialized agents:
- Summary agent
- Action-item extraction agent
- Risk and open-issue detection agent

Structured data Q&A (Text-to-SQL):
- Upload CSV/XLSX
- Or connect directly to a live database
- Ask a natural-language question
- Receive generated SQL plus a concise answer

This version supports runtime provider selection with user-supplied API keys:
- Groq
- OpenAI
- Anthropic (Claude)

## What Changed

- Provider and model are selected per request.
- API key is provided by the user at runtime and not stored on disk.
- Backend now validates file type and file size.
- Upload handling is safer (sanitized filename, random stored path).
- Streamlit UI now includes provider/model/key controls.
- Deployment files are included for API and UI containers.

## Project Structure

```text
Multi_Agentic_AI_System/
|-- agents.py
|-- api.py
|-- app.py
|-- document_processor.py
|-- llm_providers.py
|-- main.py
|-- orchestrator.py
|-- schemas.py
|-- text_to_sql.py
|-- vector_store.py
|-- requirements.txt
|-- .env.example
|-- Dockerfile.api
|-- Dockerfile.app
|-- docker-compose.yml
```

## Local Setup

1. Create and activate a virtual environment.

Windows PowerShell:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Run API:
```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

4. Run Streamlit UI:
```bash
streamlit run app.py
```

## API Usage

`POST /analyze` (multipart form-data):
- `file`: document file (`.txt`, `.pdf`, `.docx`)
- `provider`: `groq` | `openai` | `anthropic`
- `model`: optional model name (provider default used if empty)
- `api_key`: provider API key

Example with `curl`:

```bash
curl -X POST "http://localhost:8000/analyze" \
  -H "X-App-Token: YOUR_APP_TOKEN_IF_ENABLED" \
  -F "file=@test_document.txt" \
  -F "provider=groq" \
  -F "model=llama-3.3-70b-versatile" \
  -F "api_key=YOUR_KEY"
```

Get supported providers/models:
```bash
curl "http://localhost:8000/providers"
```

## Text-to-SQL API

`POST /text-to-sql` (multipart form-data):
- `file`: structured data file (`.csv`, `.xlsx`) OR
- `db_connection_uri`: full database URI (optional alternative to separate credential fields)
- `db_type`: `postgresql` | `mysql` (when using credential fields)
- `db_host`: database host
- `db_port`: database port
- `db_name`: database name
- `db_user`: database username
- `db_password`: database password
- `db_include_tables`: optional comma-separated table names for DB mode
- `question`: natural language question
- `provider`: `groq` | `openai` | `anthropic`
- `model`: optional model name (provider default used if empty)
- `api_key`: provider API key

Provide exactly one source per request: `file` or database connection details.

Example with `curl`:

```bash
curl -X POST "http://localhost:8000/text-to-sql" \
  -H "X-App-Token: YOUR_APP_TOKEN_IF_ENABLED" \
  -F "file=@sample.csv" \
  -F "question=Top 5 customers by total revenue" \
  -F "provider=openai" \
  -F "model=gpt-4o-mini" \
  -F "api_key=YOUR_KEY"
```

Example with live database connection:

```bash
curl -X POST "http://localhost:8000/text-to-sql" \
  -H "X-App-Token: YOUR_APP_TOKEN_IF_ENABLED" \
  -F "db_type=postgresql" \
  -F "db_host=localhost" \
  -F "db_port=5432" \
  -F "db_name=sales_db" \
  -F "db_user=user" \
  -F "db_password=password" \
  -F "db_include_tables=orders,customers" \
  -F "question=Top 5 customers by total revenue" \
  -F "provider=openai" \
  -F "model=gpt-4o-mini" \
  -F "api_key=YOUR_KEY"
```

## CLI Usage

```bash
python main.py test_document.txt --provider groq --model llama-3.3-70b-versatile --api-key YOUR_KEY
```

You can also omit `--api-key` and set one of:
- `GROQ_API_KEY`
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`

## Deployment with Docker Compose

1. Build and run:
```bash
docker compose up --build
```

2. Services:
- API: `http://localhost:8000`
- UI: `http://localhost:8501`

UI calls API via `BACKEND_URL` (configured in `docker-compose.yml`).

## Environment Variables

See `.env.example` for deploy configuration:
- `CORS_ORIGINS`
- `MAX_FILE_SIZE_MB`
- `RATE_LIMIT_PER_MIN`
- `APP_AUTH_TOKEN`
- `REDIS_URL` (optional, enables distributed rate limiting)
- `ENABLE_CLEANUP_ENDPOINT`
- `BACKEND_URL` (UI runtime)

## Security Notes

- API keys are passed per request and are not persisted.
- Optional API gateway token is supported via `X-App-Token` (`APP_AUTH_TOKEN`).
- Rate limiting is enabled (`RATE_LIMIT_PER_MIN`), using Redis when `REDIS_URL` is set, otherwise in-memory fallback.
- File type and size are validated.
- Uploaded temporary files are deleted after processing.
- SQL generation is restricted to read-only queries (`SELECT` / `WITH ... SELECT`).
- Cleanup endpoint is disabled by default in production (`ENABLE_CLEANUP_ENDPOINT=false`).
