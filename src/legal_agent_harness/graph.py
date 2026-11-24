"""Проверка уникальности узлов и ссылок графа. Только стандартная библиотека."""

from __future__ import annotations

from typing import Any, Dict

from .core import HarnessError


class GraphValidationError(HarnessError):
    """Нарушена структура узлов или ссылок графа."""


def validate_graph(graph: Any) -> Dict[str, Any]:
    """Проверяет ID узлов и ссылки edges.from/to, возвращает исходный граф.

    Не изменяет граф и не удаляет повторные связи. Проверка не устанавливает
    юридическую достоверность извлечения и не заменяет проверку экспертом.
    """
    if not isinstance(graph, dict):
        raise GraphValidationError("граф должен быть JSON-объектом")
    for field in ("nodes", "edges"):
        if not isinstance(graph.get(field), list):
            raise GraphValidationError(f"{field} должен быть массивом")

    seen: Dict[str, int] = {}
    for index, node in enumerate(graph["nodes"]):
        path = f"nodes[{index}]"
        if not isinstance(node, dict):
            raise GraphValidationError(f"{path} должен быть объектом")
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise GraphValidationError(f"{path}.id должен быть непустой строкой")
        if node_id in seen:
            raise GraphValidationError(
                f"{path}.id: повторяющийся ID {node_id!r} "
                f"(впервые в nodes[{seen[node_id]}].id)"
            )
        seen[node_id] = index

    for index, edge in enumerate(graph["edges"]):
        path = f"edges[{index}]"
        if not isinstance(edge, dict):
            raise GraphValidationError(f"{path} должен быть объектом")
        for field in ("from", "to"):
            node_id = edge.get(field)
            if not isinstance(node_id, str) or not node_id.strip():
                raise GraphValidationError(
                    f"{path}.{field} должен быть непустой строкой"
                )
            if node_id not in seen:
                raise GraphValidationError(
                    f"{path}.{field}: узел с ID {node_id!r} отсутствует в nodes"
                )
    return graph
