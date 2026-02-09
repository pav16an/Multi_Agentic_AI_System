import streamlit as st
import requests
import json
from pathlib import Path

# Page config
st.set_page_config(
    page_title="Multi-Agent Document Intelligence",
    page_icon="🤖",
    layout="wide"
)

# Title
st.title("🤖 Multi-Agent Document Intelligence System")
st.markdown("Upload a document to extract **summaries**, **action items**, and **risks** using AI agents")

# Sidebar
with st.sidebar:
    st.header("📋 About")
    st.markdown("""
    This system uses **3 specialized AI agents**:
    
    - 🔍 **Summary Agent** - Extracts key points
    - ✅ **Action Agent** - Finds tasks & deadlines
    - ⚠️ **Risk Agent** - Identifies risks & blockers
    
    **Powered by:** Groq API (Llama 3.3 70B)
    """)
    
    st.header("📁 Supported Files")
    st.markdown("- `.txt` - Text files\n- `.pdf` - PDF documents\n- `.docx` - Word documents")

# Main content
col1, col2 = st.columns([1, 1])

with col1:
    st.header("📤 Upload Document")
    uploaded_file = st.file_uploader(
        "Choose a file",
        type=['txt', 'pdf', 'docx', 'doc'],
        help="Upload a document to analyze"
    )
    
    analyze_button = st.button("🚀 Analyze Document", type="primary", use_container_width=True)

with col2:
    st.header("ℹ️ Instructions")
    st.markdown("""
    1. Upload a document (TXT, PDF, or DOCX)
    2. Click "Analyze Document"
    3. Wait 5-10 seconds for AI processing
    4. View results below
    
    **Example documents:** Meeting notes, project plans, requirements docs
    """)

# Analysis section
if analyze_button and uploaded_file:
    with st.spinner("🔄 Analyzing document with AI agents..."):
        try:
            # Prepare file for API
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
            
            # Call API (use localhost for local, or API_URL env var for Docker)
            api_url = "http://localhost:8000/analyze"
            response = requests.post(api_url, files=files, timeout=60)
            
            if response.status_code == 200:
                result = response.json()
                
                st.success("✅ Analysis Complete!")
                
                # Display results in tabs
                tab1, tab2, tab3, tab4 = st.tabs(["📝 Summary", "✅ Action Items", "⚠️ Risks", "📄 Raw JSON"])
                
                with tab1:
                    st.subheader("Summary")
                    st.write(result.get("summary", "No summary available"))
                
                with tab2:
                    st.subheader("Action Items")
                    action_items = result.get("action_items", [])
                    if action_items:
                        for i, item in enumerate(action_items, 1):
                            with st.expander(f"Task {i}: {item.get('task', 'N/A')}", expanded=True):
                                col_a, col_b = st.columns(2)
                                with col_a:
                                    st.write(f"**Owner:** {item.get('owner', 'Not Specified')}")
                                    st.write(f"**Deadline:** {item.get('deadline', 'Not Specified')}")
                                with col_b:
                                    st.write(f"**Dependency:** {item.get('dependency', 'None')}")
                    else:
                        st.info("No action items found")
                
                with tab3:
                    st.subheader("Risks & Open Issues")
                    risks = result.get("risks_and_open_issues", [])
                    if risks:
                        for i, risk in enumerate(risks, 1):
                            risk_type = risk.get('type', 'Unknown')
                            icon = {"Risk": "🔴", "Open Question": "❓", "Assumption": "💭", "Missing Info": "⚠️"}.get(risk_type, "⚠️")
                            st.markdown(f"{icon} **{risk_type}:** {risk.get('description', 'N/A')}")
                    else:
                        st.info("No risks or issues found")
                
                with tab4:
                    st.subheader("Raw JSON Output")
                    st.json(result)
                    
                    # Download button
                    st.download_button(
                        label="📥 Download JSON",
                        data=json.dumps(result, indent=2),
                        file_name=f"analysis_{uploaded_file.name}.json",
                        mime="application/json"
                    )
            else:
                st.error(f"❌ Error: {response.status_code} - {response.text}")
                
        except requests.exceptions.ConnectionError:
            st.error("❌ Cannot connect to API. Make sure the API is running on port 8000")
        except Exception as e:
            st.error(f"❌ Error: {str(e)}")

elif analyze_button and not uploaded_file:
    st.warning("⚠️ Please upload a file first")

# Footer
st.markdown("---")
st.markdown("Built with ❤️ using Streamlit, FastAPI, and Groq AI")
