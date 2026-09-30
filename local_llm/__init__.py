"""Talk to a local OpenAI-compatible model (LM Studio or Ollama) and produce a dashboard.

The Colab notebook still uses NVIDIA-hosted Hugging Face models. This package is
the AMD / desktop path: it calls a model already running on the same machine
(default LM Studio at http://127.0.0.1:1234/v1) using only the Python standard
library, then hands the result to ``generate_dashboard.generate_dashboard_html``.
"""

from local_llm.client import LocalLLMError, chat, list_models, resolve_endpoint
from local_llm.pipeline import analyze_contract

__all__ = [
    'LocalLLMError',
    'analyze_contract',
    'chat',
    'list_models',
    'resolve_endpoint',
]
