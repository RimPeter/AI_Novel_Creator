from __future__ import annotations

from collections import defaultdict


def _sort_locations(locations):
    return sorted(locations, key=lambda loc: ((loc.name or "").lower(), str(loc.id)))


def _build_children_map(locations):
    by_parent = defaultdict(list)
    by_id = {}
    for loc in locations:
        by_parent[loc.parent_id].append(loc)
        by_id[loc.id] = loc

    for parent_id in by_parent:
        by_parent[parent_id] = _sort_locations(by_parent[parent_id])

    return by_parent, by_id


def build_location_rows(locations):
    locations = list(locations)
    by_parent, by_id = _build_children_map(locations)
    rows = []
    visited = set()

    def visit(loc, depth, ancestors):
        if loc.id in visited:
            return
        visited.add(loc.id)
        path_parts = [*ancestors, loc.name]
        rows.append(
            {
                "location": loc,
                "depth": depth,
                "indent_px": depth * 24,
                "path": " / ".join(part for part in path_parts if part),
            }
        )
        for child in by_parent.get(loc.id, []):
            visit(child, depth + 1, path_parts)

    roots = [loc for loc in locations if loc.parent_id is None or loc.parent_id not in by_id]
    for loc in _sort_locations(roots):
        visit(loc, 0, [])

    for loc in _sort_locations(locations):
        if loc.id not in visited:
            visit(loc, 0, [])

    return rows


def build_location_label_map(locations):
    return {row["location"].id: row["path"] or row["location"].name for row in build_location_rows(locations)}


def build_location_tree(locations):
    locations = list(locations)
    by_parent, by_id = _build_children_map(locations)
    visited = set()

    def visit(loc, ancestors):
        if loc.id in visited:
            return None
        visited.add(loc.id)
        path_parts = [*ancestors, loc.name]
        return {
            "location": loc,
            "path": " / ".join(part for part in path_parts if part),
            "children": [
                child_node
                for child_node in (visit(child, path_parts) for child in by_parent.get(loc.id, []))
                if child_node is not None
            ],
        }

    roots = [loc for loc in locations if loc.parent_id is None or loc.parent_id not in by_id]
    tree = [node for node in (visit(loc, []) for loc in _sort_locations(roots)) if node is not None]

    for loc in _sort_locations(locations):
        if loc.id not in visited:
            node = visit(loc, [])
            if node is not None:
                tree.append(node)

    return tree


def collect_descendant_ids(locations, root_id):
    locations = list(locations)
    by_parent, _by_id = _build_children_map(locations)
    descendants = set()
    stack = list(by_parent.get(root_id, []))

    while stack:
        loc = stack.pop()
        if loc.id in descendants:
            continue
        descendants.add(loc.id)
        stack.extend(by_parent.get(loc.id, []))

    return descendants
