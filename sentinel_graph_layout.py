"""Pure geometry for one downward, branching conversation graph.

Evidence relations may contain cycles (a rebuttal can refer back to a proposal).
Strongly connected groups share a layer; all other dependencies point downward.
The renderer owns selection, scale and pan. No Tk, persistence or model imports.
"""
from dataclasses import dataclass
from collections import defaultdict, deque

NODE_WIDTH = 248
NODE_HEIGHT = 76
COLUMN_STEP = 278
ROW_STEP = 104
TOP = 80
STAGES = {'issue': 0, 'intent': 1, 'proposal': 1, 'position': 1,
          'condition': 2, 'question': 2, 'blocker': 2,
          'progress': 3, 'resolution': 3, 'action': 4, 'turn': 1}


def display_nodes(snapshot):
    """Include confirmed speech that the graph has not summarized yet."""
    nodes = list(snapshot['nodes'])
    used = {ref['turn_id'] for node in nodes for ref in node['evidence']}
    for turn in snapshot['turns']:
        if turn['turn_id'] not in used:
            nodes.append(dict(id=f"raw-{turn['turn_id']}", kind='turn', topic='尚未归纳的原话',
                              role=turn['role'], basis='stated', title=turn['text'], text=turn['text'],
                              evidence=[dict(turn_id=turn['turn_id'], quote=turn['text'])]))
    return nodes


@dataclass
class GraphLayout:
    positions: dict
    width: int
    height: int
    cyclic_edges: set


def _components(order, outgoing, incoming):
    """Iterative Kosaraju traversal also supports very long conversations."""
    seen, finished = set(), []
    for start in order:
        if start in seen:
            continue
        seen.add(start)
        stack = [(start, iter(outgoing[start]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                finished.append(node)
                stack.pop()
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(outgoing[child])))
    groups, membership = [], {}
    for start in reversed(finished):
        if start in membership:
            continue
        index = len(groups)
        group, stack = [], [start]
        membership[start] = index
        while stack:
            node = stack.pop()
            group.append(node)
            for parent in incoming[node]:
                if parent not in membership:
                    membership[parent] = index
                    stack.append(parent)
        groups.append(group)
    return groups, membership


def layout_graph(nodes, edges=(), previous=None, columns=4):
    """Keep all nodes, dependencies and joins in one bounded-width canvas.

    Stable input order and previous column preferences reduce lateral movement.
    A crowded layer grows vertically instead of shrinking text or hiding nodes.
    """
    order = {n['id']: i for i, n in enumerate(nodes)}
    by_id = {n['id']: n for n in nodes}
    outgoing = {ident: [] for ident in order}
    incoming = {ident: [] for ident in order}
    valid = []
    for edge in edges:
        a, b = edge['source'], edge['target']
        if a in order and b in order and a != b:
            outgoing[a].append(b)
            incoming[b].append(a)
            valid.append((a, b))
    groups, membership = _components(order, outgoing, incoming)
    links = {i: set() for i in range(len(groups))}
    degree = dict.fromkeys(links, 0)
    rank = dict.fromkeys(links, 0)
    for a, b in valid:
        source, target = membership[a], membership[b]
        if source != target and target not in links[source]:
            links[source].add(target)
            degree[target] += 1
    queue = deque(i for i in links if not degree[i])
    while queue:
        source = queue.popleft()
        for target in sorted(links[source]):
            rank[target] = max(rank[target], rank[source] + 1)
            degree[target] -= 1
            if not degree[target]:
                queue.append(target)
    layers = defaultdict(list)
    for ident in order:
        level = rank[membership[ident]]
        if not incoming[ident] and not outgoing[ident]:
            level = STAGES.get(by_id[ident]['kind'], 1)
        layers[level].append(ident)
    positions, y = {}, TOP
    previous = previous or {}
    columns = max(1, min(4, columns))
    topic_order = {topic: i for i, topic in enumerate(dict.fromkeys(n['topic'] for n in nodes))}
    for level in sorted(layers):
        def sort_key(ident):
            parents = [positions[p][0] for p in incoming[ident] if p in positions]
            preferred = sum(parents) / len(parents) if parents else 30
            return preferred, topic_order[by_id[ident]['topic']], order[ident]
        layer = sorted(layers[level], key=sort_key)
        occupied, slots = set(), {}
        old_top=min((previous[ident][1] for ident in layer if ident in previous),default=0)
        # Reserve the existing cells first: a newly appended sibling must not
        # steal an earlier card's column. Preserve rows as well as columns.
        for ident in layer:
            if ident not in previous:continue
            old_x,old_y=previous[ident]
            column=round((old_x-30)/COLUMN_STEP)
            slot=max(0,round((old_y-old_top)/ROW_STEP))*columns+column
            if 0<=column<columns and slot not in occupied:
                occupied.add(slot);slots[ident]=slot
        for ident in layer:
            if ident in slots:continue
            parents=[positions[p][0] for p in incoming[ident] if p in positions]
            preferred=round(((sum(parents)/len(parents))-30)/COLUMN_STEP) if parents else 0
            slot=max(0,min(columns-1,preferred))
            while slot in occupied:slot+=1
            occupied.add(slot);slots[ident]=slot
        for ident,slot in slots.items():
            row, column = divmod(slot, columns)
            positions[ident] = (30 + column * COLUMN_STEP, y + row * ROW_STEP)
        y += max(1, (max(occupied, default=0) // columns + 1)) * ROW_STEP
    used_columns = max((round((x - 30) / COLUMN_STEP) + 1 for x, _ in positions.values()), default=1)
    width = 60 + (used_columns - 1) * COLUMN_STEP + NODE_WIDTH
    cycles = {(a, b) for a, b in valid if membership[a] == membership[b]}
    return GraphLayout(positions, width, max(300, y + 36), cycles)
