# Worker Retrieval and Indexing

## Ziel

Der Worker nutzt inkrementelles, hybrides Retrieval statt Full-Repository-Prompting.

## Index-Vertrag

- Schema: `retrieval_index_contract.v1.json`
- Eintragstyp: `retrieval_index_entry.v1`
- Pflichtfelder: `chunk_id`, `path`, `source_hash`, `embedding_version`, `embedding`
- Deterministische Chunk-Metadaten: Sprache, Symbolname, Byte-Range, Hash

## Hybrid-Pipeline

- Kanäle: `dense`, `lexical`, `symbol`
- Vertrag: `retrieval_pipeline_contract.v1`
- Deterministische Fallback-Reihenfolge je Kanal
- Merge + Rank mit Kanalbeitrag pro Kandidat

## Inkrementelles Indexing

- Bootstrap: vollständiger Initialindex
- Refresh: nur geänderte/gelöschte/umbenannte Pfade
- Index-Status hält Retrieval- und Embedding-Version zur Kontaminationsvermeidung

## Reranking und Query-Rewrite

- Query-Rewrite erweitert technische Synonyme
- Optionaler Reranker ist profilkonfigurierbar
- Trace enthält Original- und Rewrite-Query

## Benchmark und SLO

- Benchmark-Metriken: `Recall@k`, `MRR`, `Top-k hit-rate`
- Cache-Strategie bindet an `index_version`, `embedding_model_version`, `content_hash`
- Latenz- und Qualitätsgrenzen werden profilbezogen interpretiert

## Embedding Provider Configuration

Der kanonische Provider-Contract ist in
[`docs/worker/embedding-provider-config.md`](embedding-provider-config.md)
dokumentiert. Kurzfassung:

- Default ist `local_hash`, offline und deterministisch.
- Externe/OpenAI-kompatible Embeddings benötigen ein exaktes,
  unveränderliches Hub-Deploymentprofil und eine getrennte Worker-Egress-
  Allowlist; Request-Flags können diese Freigabe nicht erteilen.
- Provider-, Modell- oder Dimensionswechsel invalidieren alte Vektoren oder
  erzeugen einen degraded Status.
- API-Key-Werte dürfen nicht in Index-State, Diagnostik oder Logs landen.

## CodeCompass Vector Retrieval

CodeCompass vector retrieval is documented in
[`docs/worker/codecompass-vector-retrieval.md`](codecompass-vector-retrieval.md).
The important boundary is that `embedding.json` is a model-neutral input with
`embedding_text`; numeric vectors are built by the active VectorStore/provider
pair. Rebuild triggers include manifest hash, provider config hash, model
version, dimensions and `embedding_text_profile`.
Mutation, Egress und Artefakttransport sind im
[Vector-Index-Taskflow](qdrant-vector-index-taskflow.md) beschrieben;
produktive Qdrant-Lesezugriffe benötigen zusätzlich den
[taskgebundenen Hub-Scope](qdrant-vector-store-hub-read.md).

## Taskgebundene Context-Bundles

Persistierter Retrieval-Kontext gehört genau einer Hub-Task. Generische
Create-, Orchestration-Ingest- und Patch-APIs dürfen weder
`context_bundle_id` noch eingebettete `worker_execution_context.context`-
Payloads setzen. Vor der Worker-Übergabe lädt der Hub das Bundle erneut aus
der kanonischen Persistenz und prüft `bundle.task_id == task.id`; fremde,
ungebundene, fehlende oder widersprüchliche Referenzen bleiben fail-closed.
Dadurch können gecachte Chunks einer Task nicht durch eine andere Task
wiederverwendet oder durch Request-Daten überschrieben werden.
