# SPDX-License-Identifier: MIT
"""Dependency graph utilities.

Provides algorithms for working with the dependency graph including
topological sorting, cycle detection, and node collection.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from pcons.core.errors import DependencyCycleError

if TYPE_CHECKING:
    from pcons.core.node import Node
    from pcons.core.target import Target


def _target_spelling(target: Target) -> str:
    """How a target is named in a cycle report."""
    return f"{target.project.name}::{target.env_qualified_name}"


def topological_sort_targets(targets: list[Target]) -> list[Target]:
    """Sort targets in dependency order (dependencies first) via Kahn's algorithm.

    Raises:
        DependencyCycleError: If there's a cycle in the dependency graph.
    """
    if not targets:
        return []

    # Keyed on identity: neither the bare name nor project::name is unique.
    # Two (sub)projects may share a short name, and inside one project two
    # targets may share a name when their environments differ.
    dependents: dict[int, set[int]] = {id(t): set() for t in targets}
    in_degree: dict[int, int] = {id(t): 0 for t in targets}
    target_map: dict[int, Target] = {id(t): t for t in targets}

    for target in targets:
        for dep in target.dependencies:
            if id(dep) in dependents:
                dependents[id(dep)].add(id(target))
                in_degree[id(target)] += 1

    # Start with targets that have no dependencies
    queue = deque(key for key, count in in_degree.items() if count == 0)
    result: list[Target] = []

    while queue:
        key = queue.popleft()
        result.append(target_map[key])

        # Reduce in-degree for all dependents
        for dependent in dependents[key]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # If we didn't process all targets, there's a cycle
    if len(result) != len(targets):
        cycle_nodes = [
            _target_spelling(target_map[key])
            for key, count in in_degree.items()
            if count > 0
        ]
        raise DependencyCycleError(cycle_nodes)

    return result


def detect_cycles_in_targets(targets: list[Target]) -> list[list[str]]:
    """Find all cycles in the target dependency graph (DFS back edges).

    Returns:
        List of cycles, each a list of target names; empty if none.
    """
    cycles: list[list[str]] = []
    # Keyed on identity, like topological_sort_targets.
    target_map: dict[int, Target] = {id(t): t for t in targets}

    # Colors: 0=white (unvisited), 1=gray (in progress), 2=black (done)
    colors: dict[int, int] = {id(t): 0 for t in targets}
    path: list[int] = []

    def target_deps(target: Target) -> list[Target]:
        # Include implicit target deps from target.depends(other_target):
        # they are real must-resolve-before edges (see Resolver._resolve_target)
        # even though they don't propagate into .dependencies.
        return [
            *target.dependencies,
            *target._implicit_target_deps,
            *target._implicit_target_deps_output_only,
        ]

    def dfs(key: int) -> None:
        colors[key] = 1  # Gray - in progress
        path.append(key)

        target = target_map.get(key)
        if target:
            for dep in target_deps(target):
                dep_key = id(dep)
                if dep_key not in colors:
                    # External dependency, skip
                    continue
                if colors[dep_key] == 1:
                    # Found a back edge - there's a cycle
                    cycle_start = path.index(dep_key)
                    cycles.append(
                        [_target_spelling(target_map[k]) for k in path[cycle_start:]]
                        + [_target_spelling(target_map[dep_key])]
                    )
                elif colors[dep_key] == 0:
                    dfs(dep_key)

        path.pop()
        colors[key] = 2  # Black - done

    for target in targets:
        if colors[id(target)] == 0:
            dfs(id(target))

    return cycles


def topological_sort_nodes(nodes: list[Node]) -> list[Node]:
    """Sort nodes in dependency order (dependencies first).

    Raises:
        DependencyCycleError: If there's a cycle in the dependency graph.
    """
    if not nodes:
        return []

    # Use node names as keys
    node_map: dict[str, Node] = {n.name: n for n in nodes}
    # node -> set of nodes that depend on it
    dependents: dict[str, set[str]] = {n.name: set() for n in nodes}
    # node -> number of dependencies
    in_degree: dict[str, int] = {n.name: 0 for n in nodes}

    for node in nodes:
        for dep in node.deps:
            if dep.name in dependents:
                dependents[dep.name].add(node.name)
                in_degree[node.name] += 1

    # Start with nodes that have no dependencies
    queue = deque(name for name, count in in_degree.items() if count == 0)
    result: list[Node] = []

    while queue:
        name = queue.popleft()
        result.append(node_map[name])

        for dependent_name in dependents[name]:
            in_degree[dependent_name] -= 1
            if in_degree[dependent_name] == 0:
                queue.append(dependent_name)

    if len(result) != len(nodes):
        cycle_nodes = [name for name, count in in_degree.items() if count > 0]
        raise DependencyCycleError(cycle_nodes)

    return result


def collect_all_nodes(targets: list[Target]) -> set[Node]:
    """Collect all nodes from targets and their dependencies, recursively."""
    result: set[Node] = set()
    visited_targets: set[str] = set()

    def collect_from_target(target: Target) -> None:
        if target.qualified_name in visited_targets:
            return
        visited_targets.add(target.qualified_name)

        # Add this target's nodes and sources
        result.update(target.nodes)
        result.update(target.sources)

        # Recursively collect from dependencies
        for dep in target.dependencies:
            collect_from_target(dep)

    for target in targets:
        collect_from_target(target)

    return result


def collect_build_order(target: Target) -> list[Target]:
    """Return all targets needed to build *target*, dependencies first,
    ending with the target itself.
    """
    all_targets: list[Target] = []
    visited: set[str] = set()

    def collect(t: Target) -> None:
        if t.qualified_name in visited:
            return
        visited.add(t.qualified_name)

        # Collect dependencies first
        for dep in t.dependencies:
            collect(dep)

        all_targets.append(t)

    collect(target)
    return all_targets
