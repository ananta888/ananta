# CodeCompass Architecture Intelligence

Graphify-inspired analysis **on top of** the existing CodeCompass graph.
Graphify is not a dependency.

Canonical input: CodeCompass graph nodes/edges from a trusted snapshot.
Communities are a derived projection (`label_propagation` v1), never
EXTRACTED evidence. Leiden remains a future adapter behind the same port.

Outputs: communities, degree centrality, god nodes, bridges, cycles,
graph diff, wiki markdown, GraphML/Cypher/Obsidian/HTML exports.
