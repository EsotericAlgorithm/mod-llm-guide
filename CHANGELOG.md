# Changelog

### 2026-09-14 - Model Compatibility and Provider Switching

* **Five provider backends**: The guide can use Anthropic, OpenAI, Google
  Gemini, OpenRouter, or a local Ollama server through documented, independent
  provider settings.
* **Model-aware requests**: OpenAI-compatible calls now select safe token,
  temperature, and reasoning parameters from the provider and model ID.
  Explicit unsupported-parameter responses receive bounded retries and
  process-local compatibility caching.
* **Reasoning-safe budgets**: `LLMGuide.OpenAI.MaxTokensMultiplier` protects
  visible answers from hidden reasoning-token use while preserving the normal
  budget for models running with supported `reasoning_effort = none`.
* **Ollama tool reliability**: Thinking can be disabled with
  `LLMGuide.Ollama.DisableThinking`. Because Ollama ignores `tool_choice`, an
  empty routing result falls through to deterministic and normal tool routing,
  and the final response round omits tool definitions entirely.
* **Server-owned Ollama context**: Removed the ineffective per-request context
  option. The README and configuration template now explain
  `OLLAMA_CONTEXT_LENGTH`, Modelfile `num_ctx`, and verification with
  `ollama ps`.
* **Configuration and documentation**: Provider examples, model-ID formats,
  configuration defaults, and runtime fallbacks are synchronized. Fine-tuned
  OpenAI IDs inherit their base-model profile.
