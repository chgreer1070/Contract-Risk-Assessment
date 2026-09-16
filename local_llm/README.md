# `local_llm/` — run the analysis on your own computer

Calls an OpenAI-compatible local server (LM Studio or Ollama) and writes the
same dashboard the Colab notebook produces. Stdlib only. Full guide:
[`../docs/local_llm.md`](../docs/local_llm.md).

```bash
python -m local_llm ping
python -m local_llm analyze contract.pdf -o dashboard.html
python -m local_llm analyze contract.txt --provider ollama --model qwen2.5:32b
```
