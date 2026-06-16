# Restricted Transformer Inference — RTIPM-Architektur

*RTIPM-001 Bestandsanalyse + RTIPM-002..008 Architektur-Referenz*

---

## Ist-Zustand: Modell- und Embedding-Nutzung

### Vorhandene Adapter / Dienste

| Datei | Zweck | Provider |
|---|---|---|
| `agent/services/embedding_provider_config_service.py` | EmbeddingProviderConfig; Default: `local_hash` (offline) | local_hash, openai_compatible |
| `agent/services/rag_index_embedding.py` | RAG-Index-Embedding | konfigurierbar |
| `agent/services/codecompass_vector_retrieval_service.py` | CodeCompass Vektoren | via EmbeddingProvider |
| `agent/services/rag_helper_index_service.py` | Artifact-backed RAG-Indizes | via rag-helper subprocess |

**Embedding-Modell aktuell:** Default ist `local_hash` (deterministischer Hash-Fingerprint, kein echtes ML-Modell). Externe Embedding-Provider (OpenAI-kompatibel) sind optionale Konfiguration mit `external_calls_allowed=False` als Default.

**HuggingFace / sentence-transformers / ONNX / PyTorch:** *Noch nicht im Repo vorhanden* als direkte Abhängigkeit. Die neuen RTIPM-Adapter setzen das als optionale Dependencies um.

### Provider-Bewertung für restricted_transformer_inference

| Provider | Geeignet für RTIPM? | Grund |
|---|---|---|
| sentence-transformers | ✅ Vollständig | Embeddings + CrossEncoder-Reranking |
| HuggingFace Transformers | ✅ Vollständig | Classification, Feature-Extraktion, Logit-Scoring |
| ONNX Runtime | ✅ Vollständig | Schnell, reproduzierbar, exportierte Modelle |
| PyTorch | ✅ Vollständig | Flexible lokale Inferenz, Hidden States |
| Ollama / LM Studio | ⚠️ Begrenzt | Kein direkter Zugriff auf Hidden States/Attention |
| OpenAI-API | ❌ Ungeeignet | Keine Hidden States, kein sauberes Logit-Scoring |

---

## RTIPM-Architektur (neue Schicht)

### Dateien

| Datei | Rolle |
|---|---|
| `agent/services/path_ai_mode_policy_service.py` | Glob-basierte pfadbezogene AI-Mode-Policy |
| `agent/services/restricted_model_inference_service.py` | Hauptservice: gated ops, audit log, mock fallback |
| `agent/services/model_inference_adapters/__init__.py` | Base-Adapter, Capabilities, Result-Typen |
| `agent/services/model_inference_adapters/sentence_transformers_adapter.py` | sentence-transformers Embedding + CrossEncoder |
| `agent/services/model_inference_adapters/huggingface_transformers_adapter.py` | HuggingFace Pipelines (classification, feature-extraction) |
| `agent/services/model_inference_adapters/onnxruntime_adapter.py` | ONNX Runtime — exportierte Modelle |
| `agent/services/model_inference_adapters/pytorch_adapter.py` | PyTorch — lokale Modelle / Checkpoints |

### AI-Modi

| Modus | Bedeutung |
|---|---|
| `full_llm` | Normale generative LLM-Nutzung inkl. freier Antwort |
| `direct_llm` | Direkter Modellaufruf (weiterhin über Backend-Policy gesteuert) |
| `embedding_only` | Nur Embedding-Modelle; keine Generierung |
| `codecompass_only` | Nur CodeCompass/RAG/Graph/Dateien/Regeln |
| `restricted_transformer_inference` | Echte Inferenz (Encoding/Scoring/Reranking) ohne Generierung |
| `deterministic_only` | Nur grep/File-Read/Graph; kein Modellaufruf |

### Adapter Capabilities

| Adapter | embeddings | classification | rerank | choice_scoring | hidden_states | attention |
|---|---|---|---|---|---|---|
| sentence-transformers | ✅ | ❌ | ✅ (CrossEncoder) | ❌ | ❌ | ❌ |
| huggingface-transformers | ✅ | ✅ | ✅ | ✅ | ✅ | optional |
| onnxruntime | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| pytorch | ✅ | ✅ | ✅ | ✅ | ✅ | optional |
| mock (test-only) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |

### Path-Policy-Konfigurationsbeispiel

```json
{
  "path_ai_modes": [
    {
      "path_glob": "src/security/**",
      "allowed_ai_modes": ["codecompass_only", "embedding_only", "restricted_transformer_inference", "deterministic_only"],
      "blocked_ai_modes": ["full_llm", "direct_llm", "chat_generation", "code_generation"],
      "allowed_model_engines": ["sentence-transformers", "huggingface-transformers", "onnxruntime", "pytorch"],
      "allow_free_text_generation": false,
      "allow_tool_decision_from_model_text": false,
      "max_input_chars": 12000,
      "llm_scope": "local_only"
    },
    {
      "path_glob": "docs/**",
      "allowed_ai_modes": ["full_llm", "direct_llm", "embedding_only", "restricted_transformer_inference"],
      "allow_free_text_generation": true
    }
  ]
}
```

### Hard Separation Contract

**RestrictedModelInferenceService gibt niemals freie Texte zurück.**

Alle Ergebnistypen (`ClassificationResult`, `RerankResult`, `ChoiceScore`, `FeatureVector`, `RiskScoreResult`) tragen das Flag `no_generation=True`. `validate_no_generation()` prüft das bei jedem `score_choices()`-Aufruf.

`model.generate()` wird in keinem Adapter aufgerufen.

### Audit-Events

| Event | Bedeutung |
|---|---|
| `model_inference_finished` | Erfolgreiche Operation mit Latenz |
| `model_inference_blocked` | Policy hat Operation blockiert |
| `model_inference_degraded` | Adapter nicht verfügbar, Mock-Fallback aktiv |

### restricted_inference_tasks

| Task | Operation | Input | Output |
|---|---|---|---|
| `candidate_rerank` | `rerank()` | query + candidates | scores + reason_code |
| `task_classify` | `classify()` | task text + optional labels | fixed label + confidence |
| `path_domain_classify` | `classify()` | path + excerpt | domain labels + sensitivity |
| `risk_score` | `risk_score()` | diff metadata + path + symbols | risk score + category |
| `semantic_boundary_detection` | `rerank()` | candidate set + graph edges | cluster scores |
| `logit_choice_score` | `score_choices()` | prompt + fixed choices | per-choice scores |

### Backward-Kompatibilität

- Ohne `path_ai_modes` in Config: alle Modi erlaubt; kein bestehendes Verhalten ändert sich
- `PathAiModePolicyService.from_config({})` → alle Pfade erlaubt
- `RestrictedModelInferenceService(use_mock_fallback=True)` → keine ML-Abhängigkeit nötig für Tests
