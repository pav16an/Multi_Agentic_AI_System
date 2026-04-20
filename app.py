import hashlib
from html import escape
import json
import os
import time
from typing import Dict, List, Optional

import requests
import streamlit as st


DEFAULT_BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
DEFAULT_APP_ACCESS_TOKEN = os.getenv("APP_AUTH_TOKEN", "")
BACKEND_HEALTH_TIMEOUT_SEC = int(os.getenv("BACKEND_HEALTH_TIMEOUT_SEC", "20"))
BACKEND_REQUEST_TIMEOUT_SEC = int(os.getenv("BACKEND_REQUEST_TIMEOUT_SEC", "180"))
BACKEND_REQUEST_RETRIES = int(os.getenv("BACKEND_REQUEST_RETRIES", "2"))
BACKEND_RETRY_DELAY_SEC = float(os.getenv("BACKEND_RETRY_DELAY_SEC", "5"))
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


def _request_backend(
    method: str,
    url: str,
    app_access_token: str,
    timeout: int,
    **kwargs,
) -> requests.Response:
    headers = kwargs.pop("headers", {})
    request_headers = {**headers, **_auth_headers(app_access_token)}
    last_error: requests.RequestException | None = None

    for attempt in range(BACKEND_REQUEST_RETRIES + 1):
        try:
            return requests.request(
                method=method,
                url=url,
                timeout=timeout,
                headers=request_headers,
                **kwargs,
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= BACKEND_REQUEST_RETRIES:
                raise
            time.sleep(BACKEND_RETRY_DELAY_SEC)

    raise last_error  # pragma: no cover


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
        response = _request_backend(
            "GET",
            f"{base_url}/providers",
            app_access_token=app_access_token,
            timeout=BACKEND_HEALTH_TIMEOUT_SEC,
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        pass
    return FALLBACK_PROVIDER_CONFIG


def _fetch_health(base_url: str, app_access_token: str) -> Optional[Dict[str, object]]:
    try:
        response = _request_backend(
            "GET",
            f"{base_url}/health",
            app_access_token=app_access_token,
            timeout=BACKEND_HEALTH_TIMEOUT_SEC,
        )
        if response.status_code == 200:
            return response.json()
    except requests.RequestException:
        return None
    return None


def _provider_models(
    provider: str,
    suggested_models: Dict[str, List[str]],
    default_models: Dict[str, str],
) -> tuple[List[str], str]:
    models = list(suggested_models.get(provider, []))
    default_model = default_models.get(provider, "")
    if default_model and default_model not in models:
        models = [default_model, *models]
    return models, default_model


def _sync_provider_model_state(prefix: str, provider: str) -> None:
    last_provider_key = f"{prefix}_provider_last"
    if st.session_state.get(last_provider_key) == provider:
        return

    st.session_state.pop(f"{prefix}_model", None)
    st.session_state.pop(f"{prefix}_model_text", None)
    st.session_state[f"{prefix}_custom_model"] = ""
    st.session_state[last_provider_key] = provider


def _render_model_selector(
    prefix: str,
    provider: str,
    suggested_models: Dict[str, List[str]],
    default_models: Dict[str, str],
) -> str:
    models, default_model = _provider_models(provider, suggested_models, default_models)
    if models:
        model_key = f"{prefix}_model"
        if st.session_state.get(model_key) not in models:
            st.session_state[model_key] = default_model or models[0]
        return st.selectbox("Suggested Model", options=models, key=model_key)

    model_key = f"{prefix}_model_text"
    if model_key not in st.session_state:
        st.session_state[model_key] = default_model or ""
    return st.text_input("Model", key=model_key)


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


def _html_text(value: object) -> str:
    return escape(str(value or "")).replace("\n", "<br>")


def _render_stat_strip(cards: List[Dict[str, str]]) -> None:
    tiles = []
    for card in cards:
        tone = card.get("tone", "neutral")
        tiles.append(
            f"""
<div class="stat-tile tone-{tone}">
  <p class="stat-label">{_html_text(card.get("label", ""))}</p>
  <p class="stat-value">{_html_text(card.get("value", ""))}</p>
  <p class="stat-caption">{_html_text(card.get("caption", ""))}</p>
</div>
"""
        )
    st.markdown(f'<div class="stat-strip">{"".join(tiles)}</div>', unsafe_allow_html=True)


def _render_signal_panel(title: str, subtitle: str, items: List[Dict[str, str]]) -> None:
    cards = []
    for item in items:
        cards.append(
            f"""
<div class="signal-card">
  <p class="signal-title">{_html_text(item.get("title", ""))}</p>
  <p class="signal-body">{_html_text(item.get("body", ""))}</p>
</div>
"""
        )
    st.markdown(
        f"""
<section class="signal-panel">
  <div class="signal-header">
    <p class="eyebrow">{_html_text(title)}</p>
    <h3>{_html_text(subtitle)}</h3>
  </div>
  <div class="signal-grid">{"".join(cards)}</div>
</section>
""",
        unsafe_allow_html=True,
    )


def _render_selection_summary(title: str, items: List[Dict[str, str]], footer: str = "") -> None:
    segments = []
    for item in items:
        segments.append(
            f"""
<div class="summary-segment">
  <p class="summary-key">{_html_text(item.get("label", ""))}</p>
  <p class="summary-value">{_html_text(item.get("value", ""))}</p>
</div>
"""
        )
    footer_html = f'<p class="summary-footer">{_html_text(footer)}</p>' if footer else ""
    st.markdown(
        f"""
<div class="selection-summary">
  <div class="selection-summary-head">
    <span class="live-dot"></span>
    <span>{_html_text(title)}</span>
  </div>
  <div class="selection-summary-grid">{"".join(segments)}</div>
  {footer_html}
</div>
""",
        unsafe_allow_html=True,
    )


def _render_highlight_card(title: str, body: str, meta: str = "") -> None:
    meta_html = f'<p class="highlight-meta">{_html_text(meta)}</p>' if meta else ""
    st.markdown(
        f"""
<div class="highlight-card">
  <p class="eyebrow">{_html_text(title)}</p>
  <div class="highlight-body">{_html_text(body)}</div>
  {meta_html}
</div>
""",
        unsafe_allow_html=True,
    )


st.set_page_config(page_title="Document Agent Studio", page_icon="DA", layout="wide")
_ensure_state()

st.markdown(
    """
<style>
:root {
  --bg: #07131c;
  --surface: rgba(12, 23, 32, 0.88);
  --surface-2: rgba(16, 30, 41, 0.92);
  --surface-3: rgba(22, 40, 54, 0.9);
  --ink: #ecf7ff;
  --muted: #98b2c7;
  --line: rgba(132, 166, 189, 0.22);
  --brand: #17c4b5;
  --brand-2: #78e6d8;
  --accent: #ffb84d;
  --ok: #43d39e;
  --warn: #ffb84d;
  --danger: #ff7c70;
  --shadow: 0 18px 40px rgba(1, 8, 14, 0.38);
}

.stApp {
  background:
    radial-gradient(circle at 12% 10%, rgba(23, 196, 181, 0.22), transparent 26%),
    radial-gradient(circle at 90% 12%, rgba(255, 184, 77, 0.16), transparent 24%),
    radial-gradient(circle at 82% 88%, rgba(69, 211, 158, 0.12), transparent 22%),
    linear-gradient(135deg, #08131c 0%, #091722 36%, #061017 100%);
  color: var(--ink);
}

[data-testid="stHeader"] {
  background: linear-gradient(90deg, rgba(7, 16, 23, 0.96) 0%, rgba(9, 21, 31, 0.96) 100%);
  border-bottom: 1px solid var(--line);
  position: sticky;
}

[data-testid="stHeader"]::before {
  content: "Multi-Agentic AI System";
  position: absolute;
  left: 54px;
  top: 50%;
  transform: translateY(-50%);
  color: #e8fbf8;
  font-size: 0.96rem;
  font-weight: 700;
  letter-spacing: 0.02em;
  pointer-events: none;
}

.block-container {
  max-width: 1280px;
  padding-top: 1.15rem;
  padding-bottom: 2.5rem;
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, rgba(7, 16, 23, 0.96) 0%, rgba(9, 21, 31, 0.98) 100%);
  border-right: 1px solid var(--line);
}

[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stCaption {
  color: var(--ink) !important;
}

.workspace-panel,
.page-header {
  border: 1px solid var(--line);
  background: linear-gradient(160deg, rgba(13, 25, 35, 0.92) 0%, rgba(17, 33, 45, 0.9) 100%);
  backdrop-filter: blur(12px);
  box-shadow: var(--shadow);
  border-radius: 24px;
  padding: 18px 20px;
  margin-bottom: 14px;
  overflow: hidden;
  position: relative;
}

.workspace-panel::after,
.page-header::after,
.step-card::after,
.chat-wrap::after,
.highlight-card::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(120, 230, 216, 0.1), transparent 40%, rgba(255, 184, 77, 0.08));
  pointer-events: none;
}

.workspace-panel h3,
.page-header h1,
.page-header h2,
.page-header p,
.highlight-card p {
  position: relative;
  z-index: 1;
}

.hero-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(280px, 0.9fr);
  gap: 16px;
  align-items: stretch;
}

.eyebrow {
  margin: 0 0 6px 0;
  font-size: 0.76rem;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--brand-2);
  font-weight: 700;
}

.title {
  font-size: 2.3rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  margin: 0;
}

.sub {
  color: var(--muted);
  margin-top: 8px;
  margin-bottom: 0;
  max-width: 62ch;
  line-height: 1.6;
}

.hero-side {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 12px;
}

.provider-pills {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.provider-pill {
  border: 1px solid rgba(120, 230, 216, 0.24);
  color: #dffcf9;
  background: rgba(23, 196, 181, 0.12);
  border-radius: 999px;
  padding: 6px 10px;
  font-size: 0.82rem;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  border-radius: 999px;
  padding: 7px 12px;
  font-size: 0.84rem;
  font-weight: 700;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.status-badge.tone-success {
  background: rgba(67, 211, 158, 0.12);
  color: #d9fff1;
}

.status-badge.tone-danger {
  background: rgba(255, 124, 112, 0.12);
  color: #ffe1dd;
}

.status-badge.tone-warn {
  background: rgba(255, 184, 77, 0.14);
  color: #fff0d3;
}

.status-dot,
.live-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 14px currentColor;
}

.instruction-panel {
  border: 1px solid var(--line);
  background: rgba(12, 23, 32, 0.78);
  border-radius: 18px;
  padding: 14px 16px;
  margin-bottom: 14px;
  box-shadow: var(--shadow);
}

.instruction-title {
  margin: 0 0 10px 0;
  font-size: 1rem;
  font-weight: 700;
  color: #dffcf9;
}

.instruction-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.instruction-item {
  border: 1px solid rgba(132, 166, 189, 0.18);
  border-radius: 16px;
  padding: 12px 12px;
  background: rgba(17, 31, 42, 0.74);
  color: var(--muted);
  font-size: 0.88rem;
  line-height: 1.5;
}

.instruction-item strong {
  color: #f0fbff;
}

.step-card {
  border: 1px solid var(--line);
  background: var(--surface);
  backdrop-filter: blur(12px);
  border-radius: 22px;
  padding: 16px 18px;
  box-shadow: var(--shadow);
  position: relative;
  overflow: hidden;
}

.section-note {
  color: var(--muted);
  font-size: 0.95rem;
  line-height: 1.55;
}

.chat-wrap {
  border: 1px solid var(--line);
  background: var(--surface-2);
  border-radius: 22px;
  padding: 14px 16px;
  box-shadow: var(--shadow);
  position: relative;
  overflow: hidden;
}

.pill {
  display: inline-block;
  margin-right: 8px;
  margin-top: 6px;
  padding: 6px 11px;
  border-radius: 999px;
  border: 1px solid rgba(120, 230, 216, 0.28);
  background: rgba(23, 196, 181, 0.12);
  color: #dcfaf7;
  font-size: 0.82rem;
}

.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button {
  background: linear-gradient(90deg, #0ea99b 0%, #18c4b6 55%, #67decf 100%) !important;
  color: #031217 !important;
  border: 1px solid rgba(123, 236, 224, 0.24) !important;
  border-radius: 14px !important;
  font-weight: 700 !important;
  box-shadow: 0 12px 30px rgba(23, 196, 181, 0.26);
  min-height: 46px;
  transition: transform 0.18s ease, filter 0.18s ease, box-shadow 0.18s ease;
}

.stButton > button:hover,
.stDownloadButton > button:hover,
.stFormSubmitButton > button:hover {
  filter: brightness(1.04);
  transform: translateY(-2px);
  box-shadow: 0 16px 34px rgba(23, 196, 181, 0.34);
}

.stFormSubmitButton > button,
[data-testid="stSidebar"] .stButton > button {
  border: 1px solid rgba(123, 236, 224, 0.3) !important;
  box-shadow:
    0 0 0 1px rgba(120, 230, 216, 0.1) inset,
    0 0 22px rgba(23, 196, 181, 0.18),
    0 12px 24px rgba(10, 78, 73, 0.28);
}

.major-actions {
  border: 1px dashed rgba(132, 166, 189, 0.36);
  border-radius: 16px;
  padding: 12px 14px;
  margin-top: 10px;
  color: var(--muted);
  font-size: 0.88rem;
  background: rgba(10, 18, 26, 0.45);
}

.step-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 999px;
  border: 1px solid rgba(120, 230, 216, 0.28);
  background: rgba(23, 196, 181, 0.12);
  color: #dffcf9;
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
  border: 1px solid rgba(120, 230, 216, 0.32);
  background: rgba(23, 196, 181, 0.16);
  color: #dffcf9;
  font-size: 0.74rem;
  line-height: 1;
}

div[data-baseweb="input"] > div,
div[data-baseweb="select"] > div,
div[data-baseweb="textarea"] > div,
div[data-testid="stFileUploaderDropzone"] {
  background: rgba(10, 19, 28, 0.9) !important;
  border: 1px solid rgba(132, 166, 189, 0.22) !important;
  border-radius: 14px !important;
  min-height: 48px;
}

div[data-baseweb="input"] input,
div[data-baseweb="select"] *,
textarea {
  color: var(--ink) !important;
}

textarea {
  line-height: 1.55 !important;
}

label, p, h1, h2, h3, h4, h5, h6, .stCaption, .stMarkdown {
  color: var(--ink) !important;
}

div[data-testid="stMetric"] {
  background: rgba(12, 23, 32, 0.78);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 12px 14px;
}

button[data-baseweb="tab"] {
  color: var(--muted) !important;
  font-weight: 600 !important;
  gap: 6px;
}

button[data-baseweb="tab"][aria-selected="true"] {
  border-bottom: 2px solid var(--brand) !important;
  color: #ecfbfa !important;
}

div[data-testid="stFileUploaderDropzone"] * {
  color: var(--muted) !important;
}

div[data-testid="stFileUploaderDropzone"] {
  padding: 18px 14px !important;
}

[data-testid="stChatMessage"] {
  border: 1px solid rgba(132, 166, 189, 0.18);
  border-radius: 18px;
  background: rgba(12, 23, 32, 0.72);
}

[data-testid="stExpander"] {
  border: 1px solid rgba(132, 166, 189, 0.18) !important;
  border-radius: 16px !important;
  background: rgba(12, 23, 32, 0.6) !important;
}

[data-testid="stNotification"] {
  border-radius: 16px !important;
}

.stat-strip {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(185px, 1fr));
  gap: 12px;
  margin: 14px 0 12px 0;
}

.stat-tile {
  border-radius: 18px;
  border: 1px solid var(--line);
  padding: 14px 14px;
  background: linear-gradient(160deg, rgba(12, 23, 32, 0.86), rgba(18, 33, 45, 0.84));
  box-shadow: var(--shadow);
}

.stat-tile.tone-success {
  border-color: rgba(67, 211, 158, 0.22);
}

.stat-tile.tone-warn {
  border-color: rgba(255, 184, 77, 0.24);
}

.stat-tile.tone-danger {
  border-color: rgba(255, 124, 112, 0.22);
}

.stat-label,
.summary-key,
.signal-title {
  margin: 0;
  color: var(--muted);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.stat-value,
.summary-value {
  margin: 8px 0 4px 0;
  font-size: 1.16rem;
  font-weight: 700;
  color: #f3fffd;
}

.stat-caption,
.summary-footer,
.signal-body {
  margin: 0;
  color: var(--muted);
  font-size: 0.88rem;
  line-height: 1.5;
}

.signal-panel {
  margin: 10px 0 14px 0;
}

.signal-header h3 {
  margin: 0 0 10px 0;
  font-size: 1.1rem;
}

.signal-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
}

.signal-card {
  border: 1px solid rgba(132, 166, 189, 0.18);
  border-radius: 16px;
  background: rgba(12, 23, 32, 0.68);
  padding: 14px 14px;
}

.selection-summary {
  margin: 14px 0 10px 0;
  border: 1px solid rgba(132, 166, 189, 0.18);
  border-radius: 18px;
  background: rgba(10, 18, 26, 0.58);
  padding: 14px 14px;
}

.selection-summary-head {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 0.84rem;
  font-weight: 700;
  color: #defcf8;
  margin-bottom: 12px;
}

.selection-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}

.summary-segment {
  border-radius: 14px;
  padding: 12px 12px;
  background: rgba(17, 31, 42, 0.78);
  border: 1px solid rgba(132, 166, 189, 0.14);
}

.highlight-card {
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 18px 18px;
  background: linear-gradient(155deg, rgba(13, 25, 35, 0.95) 0%, rgba(16, 30, 41, 0.92) 100%);
  box-shadow: var(--shadow);
  position: relative;
  overflow: hidden;
  margin-bottom: 14px;
}

.highlight-body {
  position: relative;
  z-index: 1;
  font-size: 1rem;
  line-height: 1.75;
  color: #eef9ff;
}

.highlight-meta {
  position: relative;
  z-index: 1;
  margin: 14px 0 0 0;
  color: var(--muted);
  font-size: 0.88rem;
}

.assistant-note {
  margin-top: 10px;
  color: var(--muted);
  font-size: 0.88rem;
}

@media (max-width: 980px) {
  .hero-grid {
    grid-template-columns: 1fr;
  }

  .title {
    font-size: 1.9rem;
  }

  .block-container {
    padding-top: 0.75rem;
  }
}
</style>
""",
    unsafe_allow_html=True,
)

base_url = _normalize_base_url(DEFAULT_BACKEND_URL)

with st.sidebar:
    st.markdown(
        """
<div class="workspace-panel">
  <p class="eyebrow">Control Center</p>
  <h3 style="margin:0 0 8px 0;">Workspace Settings</h3>
  <p class="assistant-note" style="margin-top:0;">Keep backend access, runtime auth, and reset controls in one place.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    app_access_token = st.text_input(
        "App Access Token (optional)",
        value=DEFAULT_APP_ACCESS_TOKEN,
        type="password",
        help="Only needed if backend APP_AUTH_TOKEN is enabled.",
    )
    health = _fetch_health(base_url, app_access_token)
    if health:
        st.markdown(
            '<div class="status-badge tone-success"><span class="status-dot"></span>Backend Connected</div>',
            unsafe_allow_html=True,
        )
        st.caption(f"Max file size: {health.get('max_file_size_mb', '-') } MB")
    else:
        st.markdown(
            '<div class="status-badge tone-danger"><span class="status-dot"></span>Backend Offline</div>',
            unsafe_allow_html=True,
        )
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

provider_pills = "".join(
    f'<span class="provider-pill">{_html_text(PROVIDER_DISPLAY_LABELS.get(provider, provider.title()))}</span>'
    for provider in providers
)
backend_tone = "success" if health else "danger"
backend_label = "Realtime backend healthy" if health else "Backend check required"

st.markdown(
    f"""
<div class="page-header">
  <div class="hero-grid">
    <div>
      <p class="eyebrow">AI Workbench</p>
      <p class="title">Document intelligence and structured data Q&A in one sharp workspace.</p>
      <p class="sub">Run specialized agents, review generated insights, and move into follow-up chat or SQL-backed analysis without context switching.</p>
    </div>
    <div class="hero-side">
      <div class="status-badge tone-{backend_tone}"><span class="status-dot"></span>{backend_label}</div>
      <div class="provider-pills">{provider_pills}</div>
    </div>
  </div>
</div>
""",
    unsafe_allow_html=True,
)

_render_stat_strip(
    [
        {
            "label": "Providers",
            "value": str(len(providers)),
            "caption": "Runtime-swappable LLM backends",
            "tone": "success",
        },
        {
            "label": "Document Chat",
            "value": "Ready" if st.session_state.get("analysis_source_file_bytes") else "Idle",
            "caption": "Persistent context after analysis",
            "tone": "success" if st.session_state.get("analysis_source_file_bytes") else "neutral",
        },
        {
            "label": "Text-to-SQL",
            "value": "Available",
            "caption": "File upload and live database modes",
            "tone": "warn",
        },
        {
            "label": "Backend",
            "value": "Online" if health else "Offline",
            "caption": "Health-checked from the UI",
            "tone": backend_tone,
        },
    ]
)

tab_doc, tab_sql = st.tabs(["Document Assistant", "Structured Data Q&A"])

with tab_doc:
    _render_signal_panel(
        "Document Flow",
        "A focused workflow from upload to conversation",
        [
            {
                "title": "1. Analyze",
                "body": "Run the summary, action, and risk agents once against the uploaded document.",
            },
            {
                "title": "2. Review",
                "body": "Scan structured outputs and download the JSON when you need to share or archive it.",
            },
            {
                "title": "3. Continue",
                "body": "Ask follow-up questions in persistent chat without losing the original document context.",
            },
        ],
    )
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

    top = st.columns([1, 1, 1], gap="large")
    with top[0]:
        selected_provider = st.selectbox(
            "Provider",
            options=providers,
            key="analysis_provider",
            format_func=lambda p: PROVIDER_DISPLAY_LABELS.get(str(p), str(p)),
        )
    _sync_provider_model_state("analysis", selected_provider)
    with top[1]:
        selected_model = _render_model_selector(
            "analysis",
            selected_provider,
            suggested_models,
            default_models,
        )
    with top[2]:
        custom_model = st.text_input(
            "Model Override (optional)",
            help="Use only for custom model IDs.",
            key="analysis_custom_model",
            placeholder=selected_model,
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

    selected_analysis_model = (custom_model or selected_model or "").strip()
    _render_selection_summary(
        "Live document run configuration",
        [
            {"label": "Provider", "value": PROVIDER_DISPLAY_LABELS.get(selected_provider, selected_provider)},
            {"label": "Model", "value": selected_analysis_model or "Choose a model"},
            {"label": "File", "value": uploaded_file.name if uploaded_file else "No file selected"},
        ],
        footer="The model updates as soon as the provider changes. Override only when you need a custom model ID.",
    )

    st.markdown(
        '<div class="step-badge"><span class="dot">1</span>Primary action: Analyze Document</div>',
        unsafe_allow_html=True,
    )
    analyze_clicked = st.button(
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
        else:
            if not health:
                st.warning("Backend health check failed. Trying the request anyway in case the service is waking up.")
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
                response = _request_backend(
                    "POST",
                    f"{base_url}/analyze",
                    app_access_token=app_access_token,
                    timeout=BACKEND_REQUEST_TIMEOUT_SEC,
                    data=payload,
                    files=files,
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
        _render_stat_strip(
            [
                {
                    "label": "Summary",
                    "value": "Ready",
                    "caption": "Executive readout generated",
                    "tone": "success",
                },
                {
                    "label": "Action Items",
                    "value": str(len(analysis.get("action_items", []))),
                    "caption": "Operational tasks extracted",
                    "tone": "warn",
                },
                {
                    "label": "Risks / Issues",
                    "value": str(len(analysis.get("risks_and_open_issues", []))),
                    "caption": "Potential blockers surfaced",
                    "tone": "danger",
                },
            ]
        )
        _render_highlight_card(
            "Executive summary",
            analysis.get("summary", "No summary returned."),
            meta="Use this as the fastest high-level read before diving into actions or risks.",
        )

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

        _render_selection_summary(
            "Active chat context",
            [
                {"label": "Document", "value": file_name},
                {
                    "label": "Provider",
                    "value": PROVIDER_DISPLAY_LABELS.get(
                        st.session_state.get("analysis_selected_provider", ""),
                        st.session_state.get("analysis_selected_provider", "") or "Not set",
                    ),
                },
                {
                    "label": "Messages",
                    "value": str(len(messages)),
                },
            ],
            footer="Your chat thread stays attached to the analyzed file so follow-up questions can reuse earlier context.",
        )
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
            else:
                if not health:
                    st.warning(
                        "Backend health check failed. Trying the request anyway in case the service is waking up."
                    )
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
                        }
                        if send_file:
                            request_kwargs["files"] = {
                                "file": (
                                    source_name,
                                    source_bytes,
                                    "application/octet-stream",
                                )
                            }

                        response = _request_backend(
                            "POST",
                            f"{base_url}/document-chat",
                            app_access_token=app_access_token,
                            timeout=BACKEND_REQUEST_TIMEOUT_SEC,
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
                                response = _request_backend(
                                    "POST",
                                    f"{base_url}/document-chat",
                                    app_access_token=app_access_token,
                                    timeout=BACKEND_REQUEST_TIMEOUT_SEC,
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
    _render_signal_panel(
        "Structured Data Flow",
        "Designed for fast question-to-query iteration",
        [
            {
                "title": "1. Pick a source",
                "body": "Switch between uploaded spreadsheets and live database connections without leaving the page.",
            },
            {
                "title": "2. Ask clearly",
                "body": "Use one measurable question so the generated SQL and answer stay easy to validate.",
            },
            {
                "title": "3. Inspect output",
                "body": "Review the SQL, resulting rows, and any warnings before sharing the answer downstream.",
            },
        ],
    )
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
        _sync_provider_model_state("sql", sql_provider)
        sql_selected_model = _render_model_selector(
            "sql",
            sql_provider,
            suggested_models,
            default_models,
        )
        sql_custom_model = st.text_input(
            "Model Override (optional)",
            key="sql_custom_model",
            placeholder=sql_selected_model,
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

    sql_model_preview = (sql_custom_model or sql_selected_model or "").strip()
    source_preview = "CSV/XLSX upload" if sql_source_mode == "File Upload" else "Live database connection"
    _render_selection_summary(
        "Live SQL run configuration",
        [
            {"label": "Source", "value": source_preview},
            {"label": "Provider", "value": PROVIDER_DISPLAY_LABELS.get(sql_provider, sql_provider)},
            {"label": "Model", "value": sql_model_preview or "Choose a model"},
        ],
        footer="Use the override field only when you need a provider-specific model ID outside the suggested list.",
    )

    st.markdown(
        '<div class="step-badge"><span class="dot">3</span>Primary action: Run Text-to-SQL</div>',
        unsafe_allow_html=True,
    )
    sql_clicked = st.button(
        "Run Text-to-SQL",
        type="primary",
        use_container_width=True,
    )

    if sql_clicked:
        if not sql_api_key.strip():
            st.error("Enter your API key.")
        elif not sql_question.strip():
            st.error("Enter a question.")
        elif sql_source_mode == "File Upload" and structured_file is None:
            st.error("Upload a CSV/XLSX file.")
        elif sql_source_mode == "Database Connection" and (
            not db_host.strip() or not db_name.strip() or not db_user.strip() or not db_password.strip()
        ):
            st.error("Enter host, database, username, and password.")
        else:
            if not health:
                st.warning("Backend health check failed. Trying the request anyway in case the service is waking up.")
            sql_model_to_use = (sql_custom_model or sql_selected_model or "").strip()
            payload = {
                "provider": sql_provider,
                "model": sql_model_to_use,
                "api_key": sql_api_key.strip(),
                "question": sql_question.strip(),
            }
            request_kwargs = {"data": payload}
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
                response = _request_backend(
                    "POST",
                    f"{base_url}/text-to-sql",
                    app_access_token=app_access_token,
                    timeout=BACKEND_REQUEST_TIMEOUT_SEC,
                    **request_kwargs,
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
        _render_stat_strip(
            [
                {
                    "label": "Source Type",
                    "value": source_type.title(),
                    "caption": "Execution mode used for this answer",
                    "tone": "success",
                },
                {
                    "label": "Rows Returned",
                    "value": str(len(sql_result.get("rows", []))),
                    "caption": "Preview rows available in the UI",
                    "tone": "warn",
                },
                {
                    "label": "Warnings",
                    "value": str(len(sql_result.get("warnings", []))),
                    "caption": "Review before acting on the result",
                    "tone": "danger" if sql_result.get("warnings") else "success",
                },
            ]
        )
        if source_name:
            st.caption(f"Source: {source_type} ({source_name})")

        tables = sql_result.get("tables", [])
        if tables:
            st.caption("Tables used: " + ", ".join(tables))

        _render_highlight_card(
            "Answer",
            sql_result.get("answer", "No answer returned."),
            meta="Review the generated SQL and the returned rows together before taking action.",
        )
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
