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

