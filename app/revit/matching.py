"""Deterministic grouping and candidate ranking for Revit IFC assignment."""

from collections import defaultdict
from typing import Any


HIERARCHY_FIELDS = ("ComTypeName", "ComFamlyName", "ComCategoryName")


def grouping_keyword_for_element(element: dict[str, Any]) -> str:
    """Return the documented first-pass catalogue filter for a component category."""
    category = str(element.get("ComCategoryName") or "").strip()
    if category == "模型":
        return "建筑"
    if category in {"房间", "面积"}:
        return "空间"
    if category in {"管道系统", "风管系统"}:
        return "系统"
    return "构件"


def filter_candidates_by_grouping(
    element: dict[str, Any], candidates: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str, bool]:
    """Narrow a major catalogue by its Grouping before ranking it.

    A missing configured grouping must not cause an unassigned component. In that
    exceptional case, return the original catalogue and mark the fallback.
    """
    keyword = grouping_keyword_for_element(element)
    filtered = [
        candidate
        for candidate in candidates
        if keyword in str(candidate.get("Grouping") or "")
    ]
    return (filtered or candidates), keyword, not bool(filtered)


def normalize_text(value: Any) -> str:
    """Normalize optional user/plugin text without changing its displayed value."""
    if value is None:
        return ""
    return "".join(str(value).strip().lower().split())


def hierarchy_terms(element: dict[str, Any]) -> list[str]:
    """Return the non-empty type-to-category hierarchy for an element."""
    return [str(element[field]).strip() for field in HIERARCHY_FIELDS if element.get(field) and str(element[field]).strip()]


def group_unmatched_elements(elements: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group unassigned elements by the complete available three-level path."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for element in elements:
        if element.get("IdentID"):
            continue
        terms = hierarchy_terms(element)
        key = " > ".join(terms) if terms else "未分类"
        grouped[key].append(element)
    return dict(grouped)


def route_for_element(element: dict[str, Any]) -> str:
    """Use the documented route; only level elements use the level catalogue."""
    return "level" if element.get("ComCategoryName") == "标高" else "major"


def build_parent_paths(candidates: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Build known parent-name paths without inventing parents absent from the payload."""
    by_id = {str(item.get("IdentId")): item for item in candidates if item.get("IdentId") is not None}
    paths: dict[str, list[str]] = {}
    for identifier, candidate in by_id.items():
        path: list[str] = []
        parent_id = candidate.get("ParentID")
        visited = {identifier}
        while parent_id is not None and str(parent_id) in by_id and str(parent_id) not in visited:
            parent = by_id[str(parent_id)]
            visited.add(str(parent_id))
            if parent.get("IdentName"):
                path.append(str(parent["IdentName"]))
            parent_id = parent.get("ParentID")
        paths[identifier] = list(reversed(path))
    return paths


def rank_candidates(element: dict[str, Any], candidates: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    """Rank every candidate; a low score is still retained as a mandatory fallback."""
    terms = hierarchy_terms(element)
    normalized_terms = [normalize_text(term) for term in terms]
    parent_paths = build_parent_paths(candidates)
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        identifier = str(candidate.get("IdentId", ""))
        name = str(candidate.get("IdentName") or "")
        path = parent_paths.get(identifier, [])
        searchable = [normalize_text(name), *(normalize_text(item) for item in path), normalize_text(candidate.get("Grouping"))]
        score = 0
        reasons: list[str] = []
        for index, term in enumerate(normalized_terms):
            weight = 120 - index * 25
            if term == normalize_text(name):
                score += weight
                reasons.append("标识名称精确匹配")
            elif term and any(term in value for value in searchable):
                score += max(weight - 35, 10)
                reasons.append("标识层级包含匹配")
        ranked.append({
            "identifier_id": candidate.get("IdentId"),
            "identifier_name": candidate.get("IdentName"),
            "major_id": candidate.get("MarjorId"),
            "grouping": candidate.get("Grouping"),
            "parent_path": path,
            "score": score,
            "reasons": reasons or ["无直接文本匹配，作为兜底候选保留"],
            "depth": len(path),
        })
    ranked.sort(key=lambda item: (-item["score"], item["depth"], str(item["identifier_name"] or ""), str(item["identifier_id"] or "")))
    return ranked[:limit]
