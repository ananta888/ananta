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

**HuggingFace / sentence-transformers / ONNX / PyTorch:** Adapter sind im Repo vorhanden, bleiben aber optionale Dependencies. Die Basisinstallation startet ohne diese Pakete; fehlende Libraries erscheinen als `degraded`/`unavailable` in Diagnostics.

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
| `agent/services/restricted_inference_config_service.py` | Normalisierte `restricted_inference` Config, sichere Defaults, Diagnostics |
| `agent/services/model_inference_adapter_registry.py` | Lazy Adapter Registry und Factory |
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

### restricted_inference Config

```json
{
  "restricted_inference": {
    "enabled": true,
    "default_engine": "mock",
    "default_model_id": "mock-default",
    "device": "cpu",
    "allow_mock_fallback": true,
    "allowed_engines": ["mock", "sentence-transformers", "huggingface-transformers", "onnxruntime", "pytorch"],
    "models": [
      {
        "id": "mock-default",
        "engine": "mock",
        "model": "mock-deterministic-v1",
        "enabled": true,
        "tasks": ["candidate_rerank", "task_classify", "path_domain_classify", "risk_score", "choice_score"]
      }
    ],
    "tasks": {
      "candidate_rerank": {
        "enabled": true,
        "preferred_engine": "mock",
        "fallback_to_deterministic": true,
        "max_candidates": 20,
        "weight": 1.0
      }
    }
  }
}
```

### Optional Dependencies

Install only the required backend:

| Extra | Installs | Notes |
|---|---|---|
| `rtipm-sentence-transformers` | `sentence-transformers` | Embeddings and CrossEncoder reranking |
| `rtipm-huggingface` | `transformers`, `torch` | Classification, choice scoring, features |
| `rtipm-onnxruntime` | `onnxruntime`, `transformers` | Local ONNX files, CPU default |
| `rtipm-pytorch` | `torch`, `transformers` | Local HF/PyTorch models |
| `rtipm-all` | all of the above | Workstation/dev convenience |

CPU is the default device. CUDA/MPS must be selected explicitly in model config and should be paired with local paths for reproducible deployments.

### CodeCompass Ranking

`codecompass_ranking` controls optional reranking:

```json
{
  "codecompass_ranking": {
    "restricted_inference_rerank_enabled": false,
    "score_weights": {
      "embedding_score": 0.45,
      "graph_score": 0.2,
      "symbol_score": 0.2,
      "transformer_rerank_score": 0.0,
      "policy_penalty": -0.2
    },
    "trace_scores": false,
    "fallback_without_model": true
  }
}
```

Default is disabled, so existing CodeCompass ranking remains unchanged unless the operator opts in.

### Backward-Kompatibilität

- Ohne `path_ai_modes` in Config: alle Modi erlaubt; kein bestehendes Verhalten ändert sich
- `PathAiModePolicyService.from_config({})` → alle Pfade erlaubt
- `RestrictedModelInferenceService(use_mock_fallback=True)` → keine ML-Abhängigkeit nötig für Tests
- `codecompass_ranking.restricted_inference_rerank_enabled=false` → bestehendes Ranking bleibt unverändert
