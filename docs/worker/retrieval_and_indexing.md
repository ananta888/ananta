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
- Externe/OpenAI-kompatible Embeddings benötigen `external_calls_allowed=true`.
- `allowed_base_urls` wird gegen Scheme, Host, Port und Pfadgrenze geprüft.
- Provider-, Modell- oder Dimensionswechsel invalidieren alte Vektoren oder
  erzeugen einen degraded Status.
- API-Key-Werte dürfen nicht in Index-State, Diagnostik oder Logs landen.
