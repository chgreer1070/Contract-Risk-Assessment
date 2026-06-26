# AI-Powered Contract Risk Assessment and Legal Assistant

📌 Disclaimer
This AI-powered tool is not a replacement for legal advice. Always consult a professional lawyer for critical contract decisions.

## 📌 Project Overview
This project automates contract analysis, risk assessment, and legal Q&A using state-of-the-art **Generative AI and NLP** techniques. It extracts key clauses, identifies potential legal risks, and suggests recommendations. A fine-tuned chatbot is also included for **conversational Q&A** on contract-related queries.

## 🚀 Features
- **Contract Clause Extraction**: Identifies key clauses (liability, confidentiality, termination, etc.).
- **Risk Assessment**: Classifies risks (legal, financial, compliance) and rates severity.
- **Recommended Actions**: Provides mitigation strategies for identified risks.
- **Conversational Legal Assistant**: Fine-tuned chatbot for interactive contract-related Q&A.
- **Visual Risk Dashboard**: 12 interactive charts (risk gauge, severity×likelihood heatmap, category radar, clause treemap, contract timeline, sortable actions table, and more) rendered from the analysis output.
- **Efficient Retrieval**: Uses hybrid chunking and Milvus vector database for precise legal text retrieval.
  

## 🛠️ Tech Stack
- **LangChain** (RAG-based legal text processing)
- **Langgraph**(State-based workflows)
- **Milvus** (Vector database for efficient retrieval)
- **Hugging Face Transformers** (LLMs for text generation)
- **Docling** (Document processing & chunking)
- **Streamlit** (Interactive UI)
- **Unsloth** (Optimized fine-tuned LLM deployment)


## 🎯 How It Works
1. **Upload a contract (PDF)**
2. **AI extracts key clauses** and presents them in a structured format.
3. **Risk assessment** is performed to classify risks and suggest mitigation.
4. **Visual dashboard** renders the clauses, risks, and recommended actions as interactive charts.
5. **Fine-tuned chatbot** provides real-time answers for legal Q&A.

## 📊 Visual Dashboard
The Streamlit app exposes the analysis as an interactive dashboard in addition to the
text output.

- **In-app:** after analyzing a contract, open the **📊 Visual Dashboard** tab.
- **Standalone preview:** open `contract_visualization.html` directly in any browser to
  see the dashboard populated with bundled sample data (needs internet for the Chart.js CDN).

`generate_dashboard.py` is the bridge: `generate_dashboard_html(state)` parses the
pipeline's `key_clauses` / `risk_assessment_report` / `recommended_actions` output and
injects it into the HTML template. Contract text is HTML-escaped before embedding, so a
`</script>` in a contract cannot break out of the dashboard.

## ✨ Future Enhancements

Add support for multiple legal jurisdictions.
Improve chatbot accuracy with more fine-tuned legal datasets.
Implement contract comparison for risk benchmarking.

## 🔧 Setup & Installation

1️⃣ Install dependencies:
```bash
pip install -r requirements.txt
```

2️⃣ Set your Hugging Face token (required to call the hosted models). Never hardcode it:
```bash
export HF_TOKEN="your_huggingface_token"
```

3️⃣ Generate `app.py` from the notebook. The Streamlit app is defined in the
`%%writefile app.py` cell of `Contract_Risk_Assessment.ipynb`; run that notebook
(e.g. in Google Colab or Jupyter) to produce `app.py`, then launch it:
```bash
streamlit run app.py
```

> **Note:** The notebooks are built for a GPU environment (e.g. Google Colab).
> `Finetuned_Model_for_Legal_Chatbot.ipynb` fine-tunes a Mistral-7B adapter with
> Unsloth and 4-bit quantization, and `Contract_Risk_Assessment.ipynb` runs the
> RAG + risk-assessment workflow and serves the Streamlit UI (via pyngrok on Colab).

## 🧪 Development & Testing
The dashboard bridge is pure Python and runs on CPU without a GPU, model, or network:

```bash
pip install pytest
pytest                 # unit tests for the generate_dashboard.py parsers
```

To render and screenshot the dashboard in a headless browser, use the bundled run
skill (`.claude/skills/run-contract-risk-assessment/`):

```bash
npm install playwright chart.js
npx playwright install chromium
node .claude/skills/run-contract-risk-assessment/smoke.mjs   # screenshots -> /tmp/shots/
```

Both checks run automatically in CI (`.github/workflows/ci.yml`).


