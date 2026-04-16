# CLAUDE.md — AI Assistant Guide for Contract-Risk-Assessment

## Project Overview

This is an **AI-powered Contract Risk Assessment and Legal Assistant** built as a set of Jupyter notebooks designed to run on Google Colab (with GPU). The system analyzes legal contracts (PDFs), extracts key clauses, assesses risks, recommends mitigation actions, and provides a conversational legal Q&A chatbot powered by a fine-tuned LLM.

**Repository**: `chgreer1070/contract-risk-assessment`

## Repository Structure

```
Contract-Risk-Assessment/
├── Contract_Risk_Assessment.ipynb   # Main application notebook (Streamlit app + LangGraph pipeline)
├── Finetuned_Model_for_Legal_Chatbot.ipynb  # Model fine-tuning notebook (LoRA on Mistral-7B)
├── README.md                        # Project documentation
└── CLAUDE.md                        # This file
```

This is a notebook-only repository — there are no standalone Python modules, `requirements.txt`, tests, or CI/CD pipelines.

## Architecture

### Main Application (`Contract_Risk_Assessment.ipynb`)

The notebook generates a `app.py` Streamlit application via `%%writefile` magic, then runs it with `pyngrok` for public access from Colab. The app has three logical sections:

#### Section 1: Contract Analysis Pipeline (LangGraph)

A **LangGraph `StateGraph`** with five sequential nodes:

1. **`extract_chunks`** — Uses `DoclingLoader` with `HybridChunker` for PDF extraction, then `RecursiveCharacterTextSplitter` (chunk_size=512, overlap=100) for secondary splitting.
2. **`embedding_and_store`** — Embeds chunks with `sentence-transformers/all-mpnet-base-v2` and stores in a local **Milvus** vector DB (FLAT indexing for exact matching).
3. **`Clauses` (key_clauses)** — RAG chain using `mistralai/Mixtral-8x7B-Instruct-v0.1` via HuggingFace Endpoint to extract and summarize contract clauses.
4. **`generate_report`** — Uses `mistralai/Mistral-7B-Instruct-v0.3` to produce a risk assessment report (risk type, severity, likelihood, consequences).
5. **`recommend_actions`** — Uses `Mixtral-8x7B-Instruct-v0.1` to suggest specific mitigation actions per identified risk.

Pipeline state is defined by the `State` TypedDict:
```python
class State(TypedDict):
    pdf_path: str
    chunks: list
    vectorstore: Milvus
    key_clauses: str
    risk_assessment: dict
    risk_assessment_report: str
    recommended_actions: str
    contract_text: str
```

#### Section 2: Fine-tuned Chatbot

- Loads a **LoRA-adapted Mistral-7B** from `ramonad2024/contract_lora_model` on HuggingFace.
- Uses 4-bit quantization (`BitsAndBytesConfig`) for memory efficiency.
- Loaded via **Unsloth** `FastLanguageModel` for optimized inference.

#### Section 3: Streamlit UI

- Two-tab interface: **Analyze Contract** and **Legal Assistant**.
- Tab 1: Upload PDF → runs the full LangGraph pipeline → displays extracted clauses, risk assessment, and recommended actions via a selectbox.
- Tab 2: Chat interface backed by the fine-tuned model with `st.session_state` for conversation history.

### Fine-tuning Notebook (`Finetuned_Model_for_Legal_Chatbot.ipynb`)

Trains the legal chatbot model. Steps:

1. **Base model**: `unsloth/mistral-7b-instruct-v0.3-bnb-4bit` (4-bit quantized Mistral-7B)
2. **LoRA config**: rank=16, alpha=16, dropout=0, targeting all projection layers (`q/k/v/o/gate/up/down_proj`)
3. **Data**: 149 Q&A pairs from `contract_finetuning.json` (not in this repo), formatted with ChatML-style `<|im_start|>` / `<|im_end|>` tokens
4. **Training**: SFTTrainer with batch_size=2, gradient_accumulation=4, 60 steps, lr=2e-4, AdamW 8-bit
5. **Output**: Pushed to HuggingFace as `ramonad2024/contract_lora_model`

## Tech Stack

| Component | Library / Service |
|---|---|
| LLM Framework | LangChain, LangGraph |
| PDF Extraction | Docling (`langchain_docling`) |
| Embeddings | `sentence-transformers/all-mpnet-base-v2` |
| Vector DB | Milvus (local, FLAT index) |
| Text Generation | Mixtral-8x7B, Mistral-7B (HuggingFace Endpoints) |
| Fine-tuning | Unsloth, TRL (`SFTTrainer`), PEFT (LoRA) |
| Quantization | BitsAndBytes (4-bit) |
| UI | Streamlit |
| Tunneling | pyngrok (for Colab deployment) |

## Development Environment

- **Runtime**: Google Colab with GPU (Tesla T4 or better)
- **Python**: 3.x (Colab default)
- **GPU requirements**: Minimum 15 GB VRAM for fine-tuning; inference requires at least a T4
- **Dependencies**: Installed inline via `!pip install` in notebook cells (no `requirements.txt` exists despite README mention)

## Key Conventions

### Code Organization
- All application code lives inside notebooks — no separate `.py` modules are committed.
- The main `app.py` is generated at runtime via `%%writefile` in `Contract_Risk_Assessment.ipynb`.
- The project follows a linear, cell-by-cell execution model.

### API Keys and Secrets
- **HuggingFace tokens** are referenced as `HF_TOKEN` in the main app and as inline `token` parameters in the fine-tuning notebook.
- Tokens in the committed notebooks are masked with `"****"` or `"***"` — these must be replaced with valid tokens before running.
- The ngrok auth token is similarly masked.

### Models Used
- **Embedding**: `sentence-transformers/all-mpnet-base-v2`
- **Generation (clause extraction + actions)**: `mistralai/Mixtral-8x7B-Instruct-v0.1`
- **Generation (risk assessment)**: `mistralai/Mistral-7B-Instruct-v0.3`
- **Chatbot**: `ramonad2024/contract_lora_model` (fine-tuned Mistral-7B LoRA adapter)
- **Fine-tuning base**: `unsloth/mistral-7b-instruct-v0.3-bnb-4bit`

### Prompt Engineering
- All prompts use structured formats with emoji markers and explicit example formats.
- Prompts request specific output structures (clause type, risk level, likelihood, etc.) for consistency.

## Running the Project

### Main Application
1. Open `Contract_Risk_Assessment.ipynb` in Google Colab (GPU runtime required).
2. Replace masked tokens (`****`) with valid HuggingFace and ngrok credentials.
3. Run all cells sequentially.
4. The Streamlit app will be accessible via the ngrok public URL printed in the final cell.

### Fine-tuning
1. Open `Finetuned_Model_for_Legal_Chatbot.ipynb` in Google Colab (GPU runtime required).
2. Upload `contract_finetuning.json` (149 Q&A pairs) to the Colab environment.
3. Replace masked tokens with valid HuggingFace credentials.
4. Run all cells — the trained LoRA adapter will be saved locally and pushed to HuggingFace Hub.

## Important Notes for AI Assistants

- **No test suite exists.** There are no unit tests, integration tests, or CI pipelines.
- **No `requirements.txt`** — despite being mentioned in the README, it does not exist. Dependencies are installed inline in notebooks.
- **Notebooks contain large outputs** (screenshots, training logs, pip install output). The main notebook is ~1.1 MB.
- **The fine-tuning data** (`contract_finetuning.json`) is not included in the repository.
- **Secrets**: Some notebook cells still contain HuggingFace tokens in output cells. Be cautious not to expose these further.
- When modifying notebooks, preserve the cell execution order as downstream cells depend on state from earlier cells.
- The LangGraph pipeline is linear (no branching/conditionals), so modifications to one node may affect downstream nodes.
