import os
import ast
import sys
from typing import List
import networkx as nx

def get_imports(file_path: str, root_dir: str) -> List[str]:
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read())
        except Exception:
            return []

    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)

    # Filter only internal imports (starting with agent.)
    internal_imports = [imp for imp in imports if imp.startswith("agent.")]
    return internal_imports

def build_graph(root_dir: str) -> nx.DiGraph:
    G = nx.DiGraph()
    for root, _, files in os.walk(root_dir):
        for file in files:
            if file.endswith(".py"):
                file_path = os.path.join(root, file)
                module_name = file_path.replace(os.sep, ".").replace(".py", "")
                # Normalize module name (remove prefix before agent.)
                if "agent." in module_name:
                    module_name = "agent." + module_name.split("agent.")[1]

                G.add_node(module_name)
                imports = get_imports(file_path, root_dir)
                for imp in imports:
                    # We only care about module-level dependencies for now
                    # To be accurate, we should find the actual file for the import
                    G.add_edge(module_name, imp)
    return G

def is_known_cycle(cycle: List[str]) -> bool:
    return False

def main():
    root_dir = os.path.abspath("agent")
    if not os.path.exists(root_dir):
        print(f"Directory {root_dir} not found.")
        sys.exit(1)

    print(f"Analyzing imports in {root_dir} for cycles...")
    G = build_graph(root_dir)
    print(f"Built graph with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")

    cycles = list(nx.simple_cycles(G))

    new_cycles = []
    known_count = 0
    for cycle in cycles:
        if is_known_cycle(cycle):
            known_count += 1
        else:
            new_cycles.append(cycle)

    if new_cycles:
        print(f"Found {len(new_cycles)} new import cycles:")
        for cycle in new_cycles:
            print(" -> ".join(cycle) + " -> " + cycle[0])
        sys.exit(1)
    else:
        print("No new import cycles found.")
        sys.exit(0)

if __name__ == "__main__":
    main()
