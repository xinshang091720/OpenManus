"""End-to-end, auditable Revit IFC identifier assignment workflow."""

import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Optional

from app.revit.client import RevitApiClient, RevitApiError
from app.revit.operations import call_revit_operation
from app.revit.matching import (
    filter_candidates_by_grouping,
    group_unmatched_elements,
    hierarchy_terms,
    rank_candidates,
    route_for_element,
)


MAJOR_MAPPING = {"AR": "建筑", "ST": "结构", "AC": "通风空调", "PD": "给排水", "EL": "电气"}
Reviewer = Callable[[list[dict[str, Any]]], Awaitable[dict[str, dict[str, Any]]]]
REVIEW_TOP_K = 5
REVIEW_BATCH_SIZE = 3
MAX_PARALLEL_REVIEW_BATCHES = 3


class WorkflowError(RuntimeError):
    def __init__(self, stage: str, detail: str):
        self.stage = stage
        super().__init__(f"{stage}: {detail}")


def resolve_major(discipline: str) -> str:
    """Resolve a user-confirmed discipline without treating filenames as policy."""
    value = discipline.strip()
    if not value:
        raise WorkflowError("resolve_major", "请先由用户确认模型专业")
    code = value.upper()
    if code in MAJOR_MAPPING:
        return MAJOR_MAPPING[code]
    if value in MAJOR_MAPPING.values():
        return value
    supported = "、".join([*MAJOR_MAPPING, *MAJOR_MAPPING.values()])
    raise WorkflowError("resolve_major", f"不支持的模型专业：{value}；可用值为 {supported}")


async def llm_reviewer(payloads: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Review up to one batch of groups in a compact, structured LLM request."""
    # Keep Revit-only imports lightweight; optional Bedrock dependencies should only be
    # required when a real model review is actually requested.
    from app.llm import LLM
    prompt = (
        "You review Revit IFC identifiers. For every supplied group, select exactly one "
        "identifier_id from that group's candidates. Return JSON only, no Markdown: "
        '{"selections":[{"group_key":"...","identifier_id":"...","reason":"short"}]}.\n'
        + json.dumps({"groups": payloads}, ensure_ascii=False, separators=(",", ":"))
    )
    answer = await LLM("revit_reviewer").ask(
        messages=[{"role": "user", "content": prompt}], stream=False, temperature=0
    )
    if isinstance(answer, dict):
        decoded = answer
    elif not isinstance(answer, str):
        return {}
    else:
        try:
            decoded = json.loads(answer.strip())
        except json.JSONDecodeError:
            fenced = re.search(r"\{.*\}", answer, flags=re.DOTALL)
            if not fenced:
                return {}
            try:
                decoded = json.loads(fenced.group(0))
            except json.JSONDecodeError:
                return {}
    selections = decoded.get("selections", []) if isinstance(decoded, dict) else []
    if not isinstance(selections, list):
        return {}
    return {
        str(item.get("group_key")): item
        for item in selections
        if isinstance(item, dict) and item.get("group_key")
    }


class RevitIfcAssignmentWorkflow:
    def __init__(
        self,
        client: Optional[RevitApiClient] = None,
        reviewer: Reviewer = llm_reviewer,
        review_batch_size: int = REVIEW_BATCH_SIZE,
        max_parallel_review_batches: int = MAX_PARALLEL_REVIEW_BATCHES,
    ) -> None:
        self.client = client or RevitApiClient()
        self.reviewer = reviewer
        self.review_batch_size = max(1, review_batch_size)
        self.max_parallel_review_batches = max(1, max_parallel_review_batches)

    async def run(
        self,
        rvt_file_path: str,
        discipline: str,
        standard_id: int | None = 109003,
        clear_existing: bool = True,
    ) -> dict[str, Any]:
        major = resolve_major(discipline)
        standard_id = 109003 if standard_id is None else int(standard_id)
        # IFC matching is a rebuild operation in this delivery workflow.  Keep
        # the argument for source compatibility, but never allow a caller to
        # bypass the mandatory clear stage.
        clear_existing = True
        if clear_existing:
            try:
                await call_revit_operation(
                    self.client, "ClearParameters", self.client.clear_parameters
                )
            except RevitApiError as error:
                raise WorkflowError("clear_parameters", str(error)) from error
        identify_attempts = 1
        try:
            identified = await call_revit_operation(
                self.client,
                "OneClickIdentifier",
                self.client.one_click_identifier,
                standard_id,
                major,
            )
        except RevitApiError as error:
            # This endpoint can mutate plugin-side working state.  Retrying it
            # after a timeout or 500 risks re-entering the workflow after its
            # single ClearParameters call.
            raise WorkflowError("identify", str(error)) from error

        elements = identified.get("data")
        if not isinstance(elements, list) or not all(isinstance(item, dict) for item in elements):
            raise WorkflowError("identify", "OneClickIdentifier 未返回构件数组")
        groups = group_unmatched_elements(elements)
        assignments: list[dict[str, Any]] = []
        group_reports: list[dict[str, Any]] = []

        try:
            level_candidates = (
                (
                    await call_revit_operation(
                        self.client,
                        "GetLevelIfcIdent",
                        self.client.get_level_ifc_ident,
                        standard_id,
                    )
                ).get("data", [])
                if any(route_for_element(items[0]) == "level" for items in groups.values())
                else []
            )
            major_candidates = (
                (
                    await call_revit_operation(
                        self.client,
                        "GetIfcIdent",
                        self.client.get_ifc_ident,
                        standard_id,
                        major,
                    )
                ).get("data", [])
                if any(route_for_element(items[0]) == "major" for items in groups.values())
                else []
            )
        except RevitApiError as error:
            raise WorkflowError("fetch_candidates", str(error)) from error

        # Revit data retrieval remains serial above.  Only the independent LLM
        # review requests below may run concurrently; this never creates
        # concurrent calls into the Revit plugin.
        prepared_groups: list[dict[str, Any]] = []
        for group_key, members in groups.items():
            representative = members[0]
            route = route_for_element(representative)
            catalogue = level_candidates if route == "level" else major_candidates
            candidates, grouping_keyword, grouping_filter_fallback = filter_candidates_by_grouping(
                representative, catalogue
            )
            if not isinstance(candidates, list) or not candidates:
                raise WorkflowError("fetch_candidates", f"分组 '{group_key}' 没有可用 IFC 标识候选")
            ranked = rank_candidates(representative, candidates, limit=REVIEW_TOP_K)
            if not ranked or not ranked[0].get("identifier_id"):
                raise WorkflowError("rank_candidates", f"分组 '{group_key}' 没有含 IdentId 的候选")
            fallback = ranked[0]
            prepared_groups.append(
                {
                    "group_key": group_key,
                    "members": members,
                    "representative": representative,
                    "route": route,
                    "grouping_keyword": grouping_keyword,
                    "grouping_filter_fallback": grouping_filter_fallback,
                    "ranked": ranked,
                    "review_payload": {
                        "group_key": group_key,
                        "hierarchy_terms": hierarchy_terms(representative),
                        "route": route,
                        "program_best": fallback,
                        "candidates": ranked,
                    },
                }
            )

        batches = [
            prepared_groups[index : index + self.review_batch_size]
            for index in range(0, len(prepared_groups), self.review_batch_size)
        ]
        review_semaphore = asyncio.Semaphore(self.max_parallel_review_batches)

        async def review_batch(batch: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
            try:
                async with review_semaphore:
                    return await self.reviewer([item["review_payload"] for item in batch])
            except Exception:
                # The final deterministic candidate is used per group below.
                # A model failure must not block required IFC assignment.
                return {}

        review_results = await asyncio.gather(*(review_batch(batch) for batch in batches))
        reviewed_by_group = {
            group_key: review
            for batch_result in review_results
            if isinstance(batch_result, dict)
            for group_key, review in batch_result.items()
            if isinstance(review, dict)
        }

        for item in prepared_groups:
            group_key = item["group_key"]
            members = item["members"]
            representative = item["representative"]
            route = item["route"]
            grouping_keyword = item["grouping_keyword"]
            grouping_filter_fallback = item["grouping_filter_fallback"]
            ranked = item["ranked"]
            chosen = ranked[0]
            review = reviewed_by_group.get(group_key)
            review_status = "accepted"
            if review is None:
                review_status = "error"
                model_reason = "model review failed; deterministic fallback used"
            else:
                selected_id = str(review.get("identifier_id", ""))
                selected = next((candidate for candidate in ranked if str(candidate["identifier_id"]) == selected_id), None)
                if selected is None:
                    review_status = "invalid_response"
                    model_reason = str(review.get("reason") or "model did not select a supplied candidate")
                else:
                    chosen = selected
                    model_reason = str(review.get("reason") or "model confirmed candidate")

            for original in members:
                assignment = dict(original)
                assignment.update({"IdentName": chosen["identifier_name"], "IdentID": chosen["identifier_id"], "MarjorID": chosen["major_id"]})
                assignments.append(assignment)
            program_low_confidence = chosen["score"] <= 0
            group_reports.append({"group_key": group_key, "count": len(members), "hierarchy_terms": hierarchy_terms(representative), "route": route, "grouping_keyword": grouping_keyword, "grouping_filter_fallback": grouping_filter_fallback, "candidates": ranked, "selected": chosen, "model_reason": model_reason, "review_status": review_status, "program_low_confidence": program_low_confidence, "low_confidence": program_low_confidence or review_status != "accepted"})

        try:
            assigned = await call_revit_operation(
                self.client,
                "OneClickAssignment",
                self.client.one_click_assignment,
                standard_id,
                assignments,
            )
            assignment_message = assigned.get("msg", "一键赋值操作完成")
        except RevitApiError as error:
            raise WorkflowError("assign", str(error)) from error

        total_match = re.search(r"合计\s*(\d+)\s*个完成\s*(\d+)\s*个", assignment_message)
        total_elements = int(total_match.group(1)) if total_match else None
        completed_elements = int(total_match.group(2)) if total_match else None
        already_matched_count = (
            max(0, total_elements - len(elements)) if total_elements is not None else None
        )
        # ponytail: when plugin reports total completed elements, use it; fallback to assignments length for mock tests
        assigned_count = completed_elements if completed_elements is not None else len(assignments)
        supplementary_assigned_count = len(assignments)

        report = {
            "standard_id": standard_id,
            "marjor_name": major,
            "clear_existing": clear_existing,
            "identify_attempts": identify_attempts,
            "total_elements": total_elements,
            "already_matched_count": already_matched_count,
            "returned_unmatched_count": len(elements),
            "assigned_count": assigned_count,
            "supplementary_assigned_count": supplementary_assigned_count,
            "low_confidence_group_count": sum(1 for item in group_reports if item["low_confidence"]),
            "review_batch_size": self.review_batch_size,
            "review_batch_count": len(batches),
            "max_parallel_review_batches": self.max_parallel_review_batches,
            "groups": group_reports,
            "assignment_message": assignment_message,
        }
        return report
