# Run the contract tool on your own computer (LM Studio / Ollama)

This is the **local option**: the AI that reads the contract runs on *your*
machine (for example a GMKtec EVO-X3 with an AMD Ryzen AI Max+ 395), not on
Google Colab and not on NVIDIA-only code.

The rest of the tool is unchanged. After the local model writes the analysis,
the existing dashboard, scoring, confidence, and "needs review" flags take over.

## What you need

- This repository on the Evo X3 (or any computer that can reach the model).
- **LM Studio** (already on many Evo X3 boxes) **or** **Ollama**.
- Python 3.11+ for the dashboard half (`pip install -r requirements-dev.txt` is enough).
- For PDF contracts: `pip install pdfplumber`. Plain `.txt` files work with no extra packages.

You do **not** need Hugging Face tokens, CUDA, Unsloth, or Colab.

## Click-by-click: LM Studio on the Evo X3 (recommended)

1. **Open LM Studio.**
2. **Download a model.** In the Discover / Search tab, get a recent instruct model
   that fits in your memory. On a 128GB Evo X3, a strong starting point is
   **Qwen2.5 32B Instruct** (or Qwen2.5 14B if you want it faster). Load it.
3. **Start the local server.**
   - Open the **Developer** (or Local Server) tab.
   - Turn **Start Server** on.
   - Leave the default address: `http://127.0.0.1:1234`.
   - Enable **OpenAI-compatible** endpoints if there is a toggle (most recent
     LM Studio builds already serve `/v1/chat/completions`).
4. **Check the connection** from a terminal in this repo:

   ```bash
   python -m local_llm ping
   ```

   You should see `Reachable` and the model name. If it says it could not
   reach the server, the server is not started or is on a different port —
   pass `--base-url http://127.0.0.1:PORT/v1`.
5. **Analyze a contract:**

   ```bash
   python -m local_llm analyze "Flex - Palante - MSA.pdf" -o dashboard.html
   ```

   Or a text export:

   ```bash
   python -m local_llm analyze contract.txt -o dashboard.html
   ```
6. **Open `dashboard.html`** in your browser. Same dashboard as before —
   scores, reasoning, verified quotes, "Needs review only" filter.

A typical first run on a 32B model takes a few minutes (four model calls:
clauses, metadata, risks, actions). Leave LM Studio open while it runs.

## Optional: Ollama instead of LM Studio

```bash
ollama pull qwen2.5:32b
python -m local_llm ping --provider ollama --model qwen2.5:32b
python -m local_llm analyze contract.pdf -o dashboard.html --provider ollama --model qwen2.5:32b
```

Ollama's OpenAI-compatible URL is `http://127.0.0.1:11434/v1`.

## Settings you can change

| flag / env var | meaning |
|---|---|
| `--provider lmstudio` or `ollama` | which default URL to use |
| `--base-url` / `CONTRACT_RISK_LLM_BASE_URL` | full API root, including `/v1` |
| `--model` / `CONTRACT_RISK_LLM_MODEL` | model id (LM Studio often ignores this if only one model is loaded) |
| `--timeout` | seconds to wait per model call (default 180) |
| `--max-chars` | truncate very long contracts (default 100,000 characters) |

## What this path does *not* do

- It does not run the old NVIDIA Colab notebook (`Contract_Risk_Assessment.ipynb`).
- It does not fine-tune a model (`Finetuned_Model_for_Legal_Chatbot.ipynb` is unchanged).
- It is not legal advice. The dashboard still flags items the built-in playbook
  does not cover as **Needs review**.

## If something goes wrong

- **"Could not reach the local model"** — LM Studio / Ollama is not running, or
  the port is wrong. Use `python -m local_llm ping` to check.
- **"PDF … install pdfplumber"** — either install it or save the contract as `.txt`.
- **0 clauses / 0 risks** — the model replied in prose instead of JSON. Load a
  stronger instruct model and retry; the tool already strips markdown fences
  when it can.
- **Slow / out of memory** — pick a smaller model (14B instead of 32B) or close
  other GPU-heavy apps.
