import hashlib
import json
import os
from typing import Dict, List, Optional

import requests
import streamlit as st


DEFAULT_BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
DEFAULT_APP_ACCESS_TOKEN = os.getenv("APP_AUTH_TOKEN", "")
FALLBACK_PROVIDER_CONFIG = {
    "providers": ["groq", "openai", "anthropic"],
    "default_models": {
        "groq": "llama-3.3-70b-versatile",
        "openai": "gpt-4o-mini",
        "anthropic": "claude-3-5-sonnet-latest",
    },
    "suggested_models": {
        "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        "openai": ["gpt-4o-mini", "gpt-4.1-mini"],
        "anthropic": ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest"],
    },
}

PROVIDER_DISPLAY_LABELS = {
    "groq": "Groq",
    "openai": "OpenAI",
    "anthropic": "Claude",
}


def _normalize_base_url(url: str) -> str:
    cleaned = (url or "").strip().rstrip("/")
    cleaned = cleaned or DEFAULT_BACKEND_URL
    if not cleaned.startswith(("http://", "https://")):
        cleaned = f"http://{cleaned}"
    return cleaned


def _auth_headers(app_access_token: str) -> Dict[str, str]:
    token = (app_access_token or "").strip()
    if not token:
        return {}
    return {"X-App-Token": token}


def _extract_error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return payload.get("detail", response.text)
    except ValueError:
        pass
    return response.text


def _result_filename(uploaded_name: str) -> str:
    stem = (uploaded_name or "document").rsplit(".", 1)[0]
    return f"{stem}_analysis.json"


def _document_id(file_name: str, file_bytes: bytes) -> str:
    digest = hashlib.sha256(file_bytes).hexdigest()[:16] if file_bytes else "empty"
    return f"{file_name}:{digest}"


def _load_provider_config(base_url: str, app_access_token: str) -> Dict[str, object]:
    try:
        response = requests.get(
            f"{base_url}/providers",
            timeout=8,
            headers=_auth_headers(app_access_token),
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass
    return FALLBACK_PROVIDER_CONFIG


def _fetch_health(base_url: str, app_access_token: str) -> Optional[Dict[str, object]]:
    try:
        response = requests.get(
            f"{base_url}/health",
            timeout=6,
            headers=_auth_headers(app_access_token),
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        return None
    return None


def _ensure_state() -> None:
    defaults = {
        "analysis_result": None,
        "last_file_name": "analysis_output.json",
        "analysis_source_file_name": "",
        "analysis_source_file_bytes": b"",
        "analysis_selected_provider": "",
        "analysis_selected_model": "",
        "analysis_api_key": "",
        "analysis_doc_id": "",
        "doc_chat_threads": {},
        "sql_result": None,
        "sql_last_file": "sql_output.json",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _clear_workspace_state() -> None:
    st.session_state["analysis_result"] = None
    st.session_state["last_file_name"] = "analysis_output.json"
    st.session_state["analysis_source_file_name"] = ""
    st.session_state["analysis_source_file_bytes"] = b""
    st.session_state["analysis_selected_provider"] = ""
    st.session_state["analysis_selected_model"] = ""
    st.session_state["analysis_api_key"] = ""
    st.session_state["analysis_doc_id"] = ""
    st.session_state["doc_chat_threads"] = {}
    st.session_state["sql_result"] = None
    st.session_state["sql_last_file"] = "sql_output.json"


st.set_page_config(page_title="Document Agent Studio", page_icon="DA", layout="wide")
_ensure_state()

st.markdown(
    """
<style>
:root {
  --bg: #070a1a;
  --surface: rgba(16, 24, 58, 0.78);
  --surface-2: rgba(10, 16, 44, 0.88);
  --ink: #eef2ff;
  --muted: #aab6e9;
  --line: rgba(136, 151, 255, 0.28);
  --brand: #4b63ff;
  --brand-2: #6b7fff;
  --ok: #2fca9c;
  --warn: #ffad66;
}

.stApp {
  background:
    radial-gradient(circle at 4% 8%, rgba(112, 130, 255, 0.32), transparent 24%),
    radial-gradient(circle at 95% 95%, rgba(69, 90, 220, 0.26), transparent 28%),
    linear-gradient(118deg, #080d29 0%, #060915 42%, #070a1a 100%),
    linear-gradient(rgba(255, 255, 255, 0.028) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.028) 1px, transparent 1px);
  background-size: auto, auto, auto, 56px 56px, 56px 56px;
  color: var(--ink);
}

[data-testid="stHeader"] {
  background: linear-gradient(90deg, rgba(6, 11, 35, 0.98) 0%, rgba(4, 8, 24, 0.98) 100%);
  border-bottom: 1px solid var(--line);
  position: sticky;
}

[data-testid="stHeader"]::before {
  content: "Multi-Agentic AI System";
  position: absolute;
  left: 54px;
  top: 50%;
  transform: translateY(-50%);
  color: #dce4ff;
  font-size: 0.96rem;
  font-weight: 700;
  letter-spacing: 0.01em;
  pointer-events: none;
}

.block-container {
  max-width: 1240px;
  padding-top: 1rem;
  padding-bottom: 2rem;
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(6, 11, 35, 0.96) 0%, rgba(4, 8, 24, 0.98) 100%);
  border-right: 1px solid var(--line);
}

[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stCaption {
  color: var(--ink) !important;
}

.page-header {
  border: 1px solid var(--line);
  background: linear-gradient(145deg, rgba(23, 35, 84, 0.76) 0%, rgba(10, 16, 42, 0.85) 100%);
  backdrop-filter: blur(5px);
  box-shadow: 0 16px 32px rgba(4, 8, 28, 0.34);
  border-radius: 18px;
  padding: 14px 16px;
  margin-bottom: 10px;
}

.title {
  font-size: 1.85rem;
  font-weight: 750;
  margin: 0;
}

.sub {
  color: var(--muted);
  margin-top: 4px;
  margin-bottom: 0;
}

.instruction-panel {
  border: 1px solid var(--line);
  background: rgba(14, 21, 54, 0.7);
  border-radius: 14px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.instruction-title {
  margin: 0 0 6px 0;
  font-size: 0.98rem;
  font-weight: 700;
  color: #dfe7ff;
}

.instruction-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 8px;
}

.instruction-item {
  border: 1px solid rgba(132, 146, 255, 0.28);
  border-radius: 10px;
  padding: 8px 9px;
  background: rgba(9, 16, 42, 0.65);
  color: var(--muted);
  font-size: 0.86rem;
  line-height: 1.35;
}

.instruction-item strong {
  color: #dce5ff;
}

.step-card {
  border: 1px solid var(--line);
  background: var(--surface);
  backdrop-filter: blur(4px);
  border-radius: 16px;
  padding: 12px 14px;
  box-shadow: 0 14px 30px rgba(3, 8, 26, 0.28);
}

.section-note {
  color: var(--muted);
  font-size: 0.93rem;
}

.chat-wrap {
  border: 1px solid var(--line);
  background: var(--surface-2);
  border-radius: 16px;
  padding: 10px 12px;
  box-shadow: 0 14px 30px rgba(3, 8, 26, 0.3);
}

.pill {
  display: inline-block;
  margin-right: 8px;
  margin-top: 6px;
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid rgba(130, 146, 255, 0.42);
  background: rgba(66, 88, 214, 0.22);
  color: #d9e2ff;
  font-size: 0.82rem;
}

.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button {
  background: linear-gradient(90deg, var(--brand) 0%, var(--brand-2) 100%) !important;
  color: #fff !important;
  border: 1px solid rgba(143, 158, 255, 0.4) !important;
  border-radius: 10px !important;
  font-weight: 700 !important;
  box-shadow: 0 12px 24px rgba(60, 84, 255, 0.3);
}

.stButton > button:hover,
.stDownloadButton > button:hover,
.stFormSubmitButton > button:hover {
  filter: brightness(1.05);
  transform: translateY(-1px);
}

.stFormSubmitButton > button,
[data-testid="stSidebar"] .stButton > button {
  border: 1px solid rgba(177, 190, 255, 0.72) !important;
  box-shadow:
    0 0 0 1px rgba(108, 128, 255, 0.5) inset,
    0 0 22px rgba(88, 107, 255, 0.38),
    0 12px 24px rgba(60, 84, 255, 0.32);
}

.major-actions {
  border: 1px dashed rgba(150, 166, 255, 0.5);
  border-radius: 12px;
  padding: 8px 10px;
  margin-top: 8px;
  color: var(--muted);
  font-size: 0.86rem;
}

.step-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid rgba(155, 171, 255, 0.56);
  background: rgba(64, 85, 215, 0.24);
  color: #e2e9ff;
  font-size: 0.82rem;
  font-weight: 700;
}

.step-badge .dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 1px solid rgba(183, 194, 255, 0.72);
  background: rgba(80, 102, 255, 0.45);
  color: #ffffff;
  font-size: 0.74rem;
  line-height: 1;
}

div[data-baseweb="input"] > div,
div[data-baseweb="select"] > div,
div[data-baseweb="textarea"] > div,
div[data-testid="stFileUploaderDropzone"] {
  background: rgba(8, 14, 36, 0.85) !important;
  border: 1px solid rgba(124, 139, 235, 0.36) !important;
  border-radius: 10px !important;
}

div[data-baseweb="input"] input,
div[data-baseweb="select"] *,
textarea {
  color: var(--ink) !important;
}

label, p, h1, h2, h3, h4, h5, h6, .stCaption, .stMarkdown {
  color: var(--ink) !important;
}

div[data-testid="stMetric"] {
  background: rgba(10, 16, 44, 0.7);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 8px 10px;
}

button[data-baseweb="tab"] {
  color: var(--muted) !important;
}

button[data-baseweb="tab"][aria-selected="true"] {
  border-bottom: 2px solid var(--brand) !important;
  color: #dce4ff !important;
}

div[data-testid="stFileUploaderDropzone"] * {
  color: var(--muted) !important;
}
</style>
""",
    unsafe_allow_html=True,
)

base_url = _normalize_base_url(DEFAULT_BACKEND_URL)

with st.sidebar:
    st.markdown("### Workspace")
    app_access_token = st.text_input(
        "App Access Token (optional)",
        value=DEFAULT_APP_ACCESS_TOKEN,
        type="password",
        help="Only needed if backend APP_AUTH_TOKEN is enabled.",
    )
    health = _fetch_health(base_url, app_access_token)
    if health:
        st.success("Backend connected")
        st.caption(f"Max file size: {health.get('max_file_size_mb', '-') } MB")
    else:
        st.error("Backend not reachable")
        st.caption("Run API: `uvicorn api:app --reload --port 8000`")

    st.code(base_url, language="text")
    st.markdown(
        f"[Open API Docs]({base_url}/docs)  \n"
        f"[Health Endpoint]({base_url}/health)",
        unsafe_allow_html=False,
    )

    st.markdown(
        '<div class="step-badge"><span class="dot">0</span>Clear workspace if you want a fresh run</div>',
        unsafe_allow_html=True,
    )
    if st.button("Clear Workspace", use_container_width=True):
        _clear_workspace_state()
        st.rerun()

provider_config = _load_provider_config(base_url, app_access_token)
providers: List[str] = provider_config.get("providers", FALLBACK_PROVIDER_CONFIG["providers"])
default_models: Dict[str, str] = provider_config.get(
    "default_models", FALLBACK_PROVIDER_CONFIG["default_models"]
)
suggested_models: Dict[str, List[str]] = provider_config.get(
    "suggested_models", FALLBACK_PROVIDER_CONFIG["suggested_models"]
)

st.markdown(
    """
<div class="page-header">
  <p class="title">Chat with your document</p>
  <p class="sub">Run specialized agents first, then continue with multi-turn follow-up chat for deeper answers.</p>
</div>
""",
    unsafe_allow_html=True,
)

tab_doc, tab_sql = st.tabs(["Document Assistant", "Structured Data Q&A"])

with tab_doc:
    st.markdown(
        """
<div class="instruction-panel">
  <p class="instruction-title">Before Running Document Agents</p>
  <div class="instruction-grid">
    <div class="instruction-item"><strong>Summary Agent:</strong> Upload a readable document with clear topic/context and complete paragraphs.</div>
    <div class="instruction-item"><strong>Action Agent:</strong> Include task statements, owners, dependencies, or deadlines for accurate extraction.</div>
    <div class="instruction-item"><strong>Risk Agent:</strong> Include assumptions, blockers, constraints, and unresolved questions in the source text.</div>
    <div class="instruction-item"><strong>Chat Agent:</strong> After analysis, ask specific follow-up questions and reuse the same document thread.</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    st.markdown('<div class="step-card">', unsafe_allow_html=True)
    st.markdown("#### Step 1. Run Document Analysis")
    st.markdown(
        '<div class="section-note">Upload a file once, get summary/actions/risks, then continue with chat without losing previous answers.</div>',
        unsafe_allow_html=True,
    )

    with st.form("analysis_form", clear_on_submit=False):
        top = st.columns([1, 1, 1], gap="large")
        with top[0]:
            selected_provider = st.selectbox(
                "Provider",
                options=providers,
                key="analysis_provider",
                format_func=lambda p: PROVIDER_DISPLAY_LABELS.get(str(p), str(p)),
            )
        with top[1]:
            models = suggested_models.get(selected_provider, [])
            default_model = default_models.get(selected_provider, "")
            if default_model and default_model not in models:
                models = [default_model] + models
            if models:
                selected_model = st.selectbox("Suggested Model", options=models, key="analysis_model")
            else:
                selected_model = st.text_input("Model", value=default_model or "", key="analysis_model_text")
        with top[2]:
            custom_model = st.text_input(
                "Model Override (optional)",
                value=selected_model,
                help="Use only for custom model IDs.",
                key="analysis_custom_model",
            )

        bottom = st.columns([1.3, 1], gap="large")
        with bottom[0]:
            user_api_key = st.text_input(
                "Provider API Key",
                type="password",
                help="Used only for request processing.",
                key="analysis_api_key_input",
            )
        with bottom[1]:
            uploaded_file = st.file_uploader(
                "Upload Document",
                type=["txt", "pdf", "docx"],
                help="Allowed formats: TXT, PDF, DOCX",
                key="analysis_file_uploader",
            )

        st.markdown(
            '<div class="step-badge"><span class="dot">1</span>Primary action: Analyze Document</div>',
            unsafe_allow_html=True,
        )
        analyze_clicked = st.form_submit_button(
            "Analyze Document",
            type="primary",
            use_container_width=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)
    st.markdown(
        """
<span class="pill">Summary</span>
<span class="pill">Action Items</span>
<span class="pill">Risks & Open Issues</span>
<span class="pill">Follow-up Chat</span>
""",
        unsafe_allow_html=True,
    )
    st.markdown(
        """
<div class="major-actions">
  Major buttons: <code>Analyze Document</code>, <code>New Chat</code>, <code>Run Text-to-SQL</code>, <code>Clear Workspace</code>
</div>
""",
        unsafe_allow_html=True,
    )

    if analyze_clicked:
        if uploaded_file is None:
            st.error("Upload a document before running analysis.")
        elif not user_api_key.strip():
            st.error("Enter your API key for the selected provider.")
        elif not health:
            st.error("Backend is not reachable. Start API and try again.")
        else:
            model_to_use = (custom_model or selected_model or "").strip()
            file_bytes = uploaded_file.getvalue()
            payload = {
                "provider": selected_provider,
                "model": model_to_use,
                "api_key": user_api_key.strip(),
            }
            files = {
                "file": (
                    uploaded_file.name,
                    file_bytes,
                    uploaded_file.type or "application/octet-stream",
                )
            }
            progress = st.progress(5, text="Preparing analysis...")
            try:
                progress.progress(25, text="Uploading document...")
                response = requests.post(
                    f"{base_url}/analyze",
                    data=payload,
                    files=files,
                    timeout=180,
                    headers=_auth_headers(app_access_token),
                )
                progress.progress(80, text="Processing response...")
                if response.status_code != 200:
                    st.error(f"Analysis failed: {_extract_error_message(response)}")
                else:
                    result = response.json()
                    current_doc_id = _document_id(uploaded_file.name, file_bytes)
                    st.session_state["analysis_result"] = result
                    st.session_state["last_file_name"] = _result_filename(uploaded_file.name)
                    st.session_state["analysis_source_file_name"] = uploaded_file.name
                    st.session_state["analysis_source_file_bytes"] = file_bytes
                    st.session_state["analysis_selected_provider"] = selected_provider
                    st.session_state["analysis_selected_model"] = model_to_use
                    st.session_state["analysis_api_key"] = user_api_key.strip()
                    st.session_state["analysis_doc_id"] = current_doc_id
                    threads = st.session_state.get("doc_chat_threads", {})
                    if current_doc_id not in threads:
                        threads[current_doc_id] = []
                        st.session_state["doc_chat_threads"] = threads
                    st.success("Analysis complete. Chat is now ready for this document.")
                progress.progress(100, text="Done.")
            except requests.RequestException as exc:
                st.error("Could not connect to backend API.")
                st.info(str(exc))
            finally:
                progress.empty()

    analysis = st.session_state.get("analysis_result")
    if analysis:
        st.markdown("### Step 2. Review Analysis")
        m1, m2, m3 = st.columns(3)
        m1.metric("Summary", "Ready")
        m2.metric("Action Items", len(analysis.get("action_items", [])))
        m3.metric("Risks / Issues", len(analysis.get("risks_and_open_issues", [])))

        tab_summary, tab_actions, tab_risks, tab_json = st.tabs(
            ["Summary", "Action Items", "Risks", "Raw JSON"]
        )
        with tab_summary:
            st.write(analysis.get("summary", "No summary returned."))
        with tab_actions:
            actions = analysis.get("action_items", [])
            if not actions:
                st.info("No action items detected.")
            else:
                st.dataframe(actions, use_container_width=True)
        with tab_risks:
            risks = analysis.get("risks_and_open_issues", [])
            if not risks:
                st.info("No risks or open issues detected.")
            else:
                types = sorted(
                    {
                        item.get("type", "Unknown")
                        for item in risks
                        if isinstance(item, dict)
                    }
                )
                chosen = st.multiselect(
                    "Filter types",
                    options=types,
                    default=types,
                    key="risk_type_filter",
                )
                filtered = [item for item in risks if item.get("type", "Unknown") in set(chosen)]
                if filtered:
                    st.dataframe(filtered, use_container_width=True)
                else:
                    st.warning("No items match current filter.")
        with tab_json:
            st.json(analysis)
            st.download_button(
                label="Download Analysis JSON",
                data=json.dumps(analysis, indent=2),
                file_name=st.session_state["last_file_name"],
                mime="application/json",
                use_container_width=True,
            )

    if st.session_state.get("analysis_source_file_bytes"):
        st.markdown("### Step 3. Ask Follow-up Questions (Persistent Chat)")
        st.markdown('<div class="chat-wrap">', unsafe_allow_html=True)
        file_name = st.session_state.get("analysis_source_file_name", "document")
        doc_id = st.session_state.get("analysis_doc_id", "")
        threads = st.session_state.get("doc_chat_threads", {})
        if doc_id not in threads:
            threads[doc_id] = []
            st.session_state["doc_chat_threads"] = threads
        messages = threads.get(doc_id, [])

        st.caption(f"Active document: `{file_name}`")
        chat_controls = st.columns([1, 3], gap="large")
        with chat_controls[0]:
            st.markdown(
                '<div class="step-badge"><span class="dot">2</span>Start a new chat thread</div>',
                unsafe_allow_html=True,
            )
            if st.button("New Chat", use_container_width=True, key=f"new_chat_{doc_id}"):
                threads[doc_id] = []
                st.session_state["doc_chat_threads"] = threads
                st.rerun()
        with chat_controls[1]:
            st.caption("Your previous Q&A for this document will stay visible across new questions.")

        for idx, msg in enumerate(messages):
            role = msg.get("role", "assistant")
            with st.chat_message(role):
                st.write(msg.get("content", ""))
                if role == "assistant":
                    sources = msg.get("sources", [])
                    if sources:
                        with st.expander(f"Sources #{idx + 1}"):
                            for source in sources:
                                st.caption(source)

        followup_question = st.chat_input(
            "Ask a follow-up question about this document...",
            key="doc_followup_input",
        )
        if followup_question:
            provider = st.session_state.get("analysis_selected_provider", "") or selected_provider
            model = st.session_state.get("analysis_selected_model", "") or (
                custom_model or selected_model or ""
            )
            api_key = st.session_state.get("analysis_api_key", "") or user_api_key.strip()
            source_bytes = st.session_state.get("analysis_source_file_bytes", b"")
            source_name = st.session_state.get("analysis_source_file_name", "document.txt")

            if not api_key:
                st.error("API key is missing. Re-run analysis with a valid API key.")
            elif not health:
                st.error("Backend is not reachable. Start API and try again.")
            else:
                messages.append({"role": "user", "content": followup_question.strip()})
                threads[doc_id] = messages
                st.session_state["doc_chat_threads"] = threads

                payload = {
                    "question": followup_question.strip(),
                    "chat_history": json.dumps(messages[-12:], ensure_ascii=False),
                    "doc_id": doc_id,
                    "provider": provider,
                    "model": model.strip(),
                    "api_key": api_key.strip(),
                }
                assistant_turns = [msg for msg in messages if msg.get("role") == "assistant"]
                send_file = len(assistant_turns) == 0
                with st.spinner("Thinking..."):
                    try:
                        request_kwargs = {
                            "data": payload,
                            "timeout": 180,
                            "headers": _auth_headers(app_access_token),
                        }
                        if send_file:
                            request_kwargs["files"] = {
                                "file": (
                                    source_name,
                                    source_bytes,
                                    "application/octet-stream",
                                )
                            }

                        response = requests.post(
                            f"{base_url}/document-chat",
                            **request_kwargs,
                        )
                        if response.status_code != 200 and not send_file:
                            message_text = _extract_error_message(response)
                            if "context not found in cache" in (message_text or "").lower():
                                retry_kwargs = dict(request_kwargs)
                                retry_kwargs["files"] = {
                                    "file": (
                                        source_name,
                                        source_bytes,
                                        "application/octet-stream",
                                    )
                                }
                                response = requests.post(
                                    f"{base_url}/document-chat",
                                    **retry_kwargs,
                                )
                        if response.status_code != 200:
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": f"Document chat failed: {_extract_error_message(response)}",
                                    "sources": [],
                                }
                            )
                        else:
                            payload_json = response.json()
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": payload_json.get("answer", "No answer returned."),
                                    "sources": payload_json.get("source_chunks", []),
                                }
                            )
                        threads[doc_id] = messages
                        st.session_state["doc_chat_threads"] = threads
                        st.rerun()
                    except requests.RequestException as exc:
                        messages.append(
                            {
                                "role": "assistant",
                                "content": f"Could not connect to backend API. {exc}",
                                "sources": [],
                            }
                        )
                        threads[doc_id] = messages
                        st.session_state["doc_chat_threads"] = threads
                        st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.info("Run document analysis first to enable follow-up chat.")

with tab_sql:
    st.markdown("### Structured Data Q&A (Text-to-SQL)")
    st.caption(
        "Use CSV/XLSX upload or a live database connection, ask a question, and get SQL + answer."
    )
    st.markdown(
        """
<div class="instruction-panel">
  <p class="instruction-title">Before Running Text-to-SQL Agent</p>
  <div class="instruction-grid">
    <div class="instruction-item"><strong>Data source:</strong> Choose only one source: file upload or database connection.</div>
    <div class="instruction-item"><strong>Question quality:</strong> Ask one measurable question with clear metric and timeframe.</div>
    <div class="instruction-item"><strong>Schema readiness:</strong> Ensure column names are meaningful and tables are accessible.</div>
    <div class="instruction-item"><strong>Safety:</strong> Provide least-privilege DB credentials and verify query output before use.</div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )
    sql_source_mode = st.radio(
        "Data Source",
        options=["File Upload", "Database Connection"],
        horizontal=True,
        key="sql_source_mode",
    )

    with st.form("sql_form", clear_on_submit=False):
        left, right = st.columns([1.25, 1], gap="large")
        structured_file = None
        db_include_tables = ""
        db_type = ""
        db_host = ""
        db_port = ""
        db_name = ""
        db_user = ""
        db_password = ""

        with left:
            sql_provider = st.selectbox(
                "Provider",
                options=providers,
                key="sql_provider",
                format_func=lambda p: PROVIDER_DISPLAY_LABELS.get(str(p), str(p)),
            )
            sql_models = suggested_models.get(sql_provider, [])
            sql_default_model = default_models.get(sql_provider, "")
            if sql_default_model and sql_default_model not in sql_models:
                sql_models = [sql_default_model] + sql_models
            if sql_models:
                sql_selected_model = st.selectbox(
                    "Suggested Model",
                    options=sql_models,
                    key="sql_model",
                )
            else:
                sql_selected_model = st.text_input(
                    "Model",
                    value=sql_default_model or "",
                    key="sql_model_text",
                )
            sql_custom_model = st.text_input(
                "Model Override (optional)",
                value=sql_selected_model,
                key="sql_custom_model",
            )
            sql_question = st.text_area(
                "Question",
                placeholder="e.g., Top 5 customers by total revenue",
                key="sql_question",
            )

        with right:
            sql_api_key = st.text_input(
                "Provider API Key",
                type="password",
                key="sql_api_key_input",
            )
            if sql_source_mode == "File Upload":
                structured_file = st.file_uploader(
                    "Upload CSV/XLSX",
                    type=["csv", "xlsx"],
                    key="sql_file_uploader",
                )
            else:
                db_type = st.selectbox("DB Type", options=["postgresql", "mysql"], key="sql_db_type")
                default_port = "5432" if db_type == "postgresql" else "3306"
                db_host = st.text_input("Host", placeholder="localhost", key="sql_db_host")
                db_port = st.text_input("Port", value=default_port, key="sql_db_port")
                db_name = st.text_input("Database", placeholder="sales_db", key="sql_db_name")
                db_user = st.text_input("Username", placeholder="db_user", key="sql_db_user")
                db_password = st.text_input("Password", type="password", key="sql_db_password")
                db_include_tables = st.text_input(
                    "Include Tables (optional)",
                    placeholder="orders, customers",
                    key="sql_db_tables",
                )

        st.markdown(
            '<div class="step-badge"><span class="dot">3</span>Primary action: Run Text-to-SQL</div>',
            unsafe_allow_html=True,
        )
        sql_clicked = st.form_submit_button(
            "Run Text-to-SQL",
            type="primary",
            use_container_width=True,
        )

    if sql_clicked:
        if not sql_api_key.strip():
            st.error("Enter your API key.")
        elif not sql_question.strip():
            st.error("Enter a question.")
        elif not health:
            st.error("Backend is not reachable.")
        elif sql_source_mode == "File Upload" and structured_file is None:
            st.error("Upload a CSV/XLSX file.")
        elif sql_source_mode == "Database Connection" and (
            not db_host.strip() or not db_name.strip() or not db_user.strip() or not db_password.strip()
        ):
            st.error("Enter host, database, username, and password.")
        else:
            sql_model_to_use = (sql_custom_model or sql_selected_model or "").strip()
            payload = {
                "provider": sql_provider,
                "model": sql_model_to_use,
                "api_key": sql_api_key.strip(),
                "question": sql_question.strip(),
            }
            request_kwargs = {"data": payload, "timeout": 180}
            if sql_source_mode == "File Upload":
                request_kwargs["files"] = {
                    "file": (
                        structured_file.name,
                        structured_file.getvalue(),
                        structured_file.type or "application/octet-stream",
                    )
                }
            else:
                payload["db_type"] = db_type.strip()
                payload["db_host"] = db_host.strip()
                payload["db_port"] = db_port.strip()
                payload["db_name"] = db_name.strip()
                payload["db_user"] = db_user.strip()
                payload["db_password"] = db_password.strip()
                payload["db_include_tables"] = db_include_tables.strip()

            progress = st.progress(5, text="Preparing request...")
            try:
                progress.progress(25, text="Sending data...")
                response = requests.post(
                    f"{base_url}/text-to-sql",
                    **request_kwargs,
                    headers=_auth_headers(app_access_token),
                )
                progress.progress(80, text="Processing response...")
                if response.status_code != 200:
                    st.error(f"Text-to-SQL failed: {_extract_error_message(response)}")
                else:
                    st.session_state["sql_result"] = response.json()
                    st.session_state["sql_last_file"] = "text_to_sql_result.json"
                    st.success("Text-to-SQL complete.")
                progress.progress(100, text="Done.")
            except requests.RequestException as exc:
                st.error("Could not connect to backend API.")
                st.info(str(exc))
            finally:
                progress.empty()

    sql_result = st.session_state.get("sql_result")
    if sql_result:
        st.markdown("### Text-to-SQL Results")
        source_type = sql_result.get("source_type", "file")
        source_name = sql_result.get("source_name", "")
        if source_name:
            st.caption(f"Source: {source_type} ({source_name})")

        tables = sql_result.get("tables", [])
        if tables:
            st.caption("Tables used: " + ", ".join(tables))

        st.write(sql_result.get("answer", "No answer returned."))
        st.markdown("**Generated SQL**")
        st.code(sql_result.get("sql", ""), language="sql")

        if sql_result.get("column_mapping"):
            st.markdown("**Column Mapping**")
            st.dataframe(
                [
                    {"original": key, "sanitized": value}
                    for key, value in sql_result.get("column_mapping", {}).items()
                ],
                use_container_width=True,
            )

        rows = sql_result.get("rows", [])
        if rows:
            st.markdown("**Rows**")
            st.dataframe(rows, use_container_width=True)
        else:
            st.info("No rows returned.")

        warnings = sql_result.get("warnings", [])
        if warnings:
            for warning in warnings:
                st.warning(warning)

        st.download_button(
            label="Download Text-to-SQL JSON",
            data=json.dumps(sql_result, indent=2),
            file_name=st.session_state["sql_last_file"],
            mime="application/json",
            use_container_width=True,
        )

st.divider()
st.caption("Deploy tip: Set `BACKEND_URL` for Streamlit and `CORS_ORIGINS` for FastAPI.")
