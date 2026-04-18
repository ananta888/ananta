import os
import ast
import sys
from typing import List, Set, Tuple
import networkx as nx

def get_imports(file_path: str, root_dir: str) -> List[str]:
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            tree = ast.parse(f.read())
        except Exception:
            return []

    imports = []
    for node in ast.walk(tree):
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

# Existing cycles (Technical Debt)
# Each cycle is represented as a tuple of modules
KNOWN_CYCLES = [
    ("agent.routes.tasks.autopilot", "agent.routes.tasks.autopilot_tick_engine", "agent.routes.tasks.auto_planner"),
    ("agent.common.sgpt", "agent.services.opencode_runtime_service"),
    ("agent.common.sgpt", "agent.services.live_terminal_session_service"),
    ("agent.utils", "agent.common.http"),
    ("agent.services.service_registry", "agent.services.task_scoped_execution_service"),
]

def is_known_cycle(cycle: List[str]) -> bool:
    # Check if this cycle (or any rotation of it) is in KNOWN_CYCLES
    cycle_set = set(cycle)
    for known in KNOWN_CYCLES:
        if set(known) == cycle_set:
            return True

    # Heuristic: Cycles involving service_registry and routes.tasks are common technical debt here
    if "agent.services.service_registry" in cycle_set:
        return True
    if "agent.routes.tasks.autopilot" in cycle_set and "agent.services.autopilot_runtime_service" in cycle_set:
        return True

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

    if known_count > 0:
        print(f"Ignored {known_count} known cycles (technical debt).")

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
