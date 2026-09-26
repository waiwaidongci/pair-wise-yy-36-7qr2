from __future__ import annotations

from typing import Any, Dict, Optional

from .domain import (ConflictError, ensure_role, normalize_severity,
                     require_number, require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, ENTITY, RECORD_ROLES,
                    REVISION_REVIEW_ROLES, REVISION_ROLES, TERMINAL_STATES,
                    TITLE, VIEW_ROLES, can_transition, completion_blockers,
                    conclusion_snapshot, escalation_required, priority_score,
                    response_deadline_hours, role_for_transition,
                    should_rejudge_open_records, validate_transition)


class Service:
    def __init__(self, repository: Repository):
        self.repository = repository

    def _view(self, role: str) -> None:
        ensure_role(role, VIEW_ROLES)

    def create_item(self, payload: Dict[str, Any], actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, CREATE_ROLES)
        actor = require_text(actor, "actor", 100)
        title = require_text(payload.get("title"), "title", 200)
        description = require_text(payload.get("description"), "description")
        severity = normalize_severity(payload.get("severity"))
        quantity = require_number(payload.get("quantity", 0), "quantity")
        threshold = require_number(payload.get("threshold", 1), "threshold", 0.000001)
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        item = self.repository.create_item(title, description, severity, quantity,
                                           threshold, external_ref, actor)
        self.repository.append_audit("create", ENTITY, item["id"], actor, {
            "title": title, "severity": severity, "quantity": quantity,
            "priority": priority_score(severity, quantity, threshold),
        })
        return self.enrich(item)

    def add_record(self, item_id: int, payload: Dict[str, Any], actor: str,
                   role: str) -> Dict[str, Any]:
        ensure_role(role, RECORD_ROLES)
        actor = require_text(actor, "actor", 100)
        kind = require_text(payload.get("kind"), "kind", 100)
        detail = require_text(payload.get("detail"), "detail")
        status = payload.get("status", "open")
        if status not in ("open", "closed"):
            raise ValueError("status必须是open或closed")
        external_ref = payload.get("external_ref")
        if external_ref is not None:
            external_ref = require_text(external_ref, "external_ref", 100)
        record = self.repository.add_record(item_id, kind, detail, status,
                                            external_ref, actor)
        self.repository.append_audit("record", ENTITY, item_id, actor, {
            "record_id": record["id"], "kind": kind, "status": status,
        })
        return record

    def transition(self, item_id: int, target: str, expected_version: int,
                   actor: str, role: str) -> Dict[str, Any]:
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        validate_transition(item["status"], target)
        ensure_role(role, role_for_transition(target))
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValueError("expected_version必须是正整数")
        blockers = completion_blockers(target, self.repository.open_record_count(item_id),
                                       self.repository.pending_revision_count(item_id))
        if blockers:
            raise ConflictError("；".join(blockers))
        updated = self.repository.transition_item(item_id, target, expected_version, actor)
        self.repository.append_audit("transition", ENTITY, item_id, actor, {
            "from": item["status"], "to": target,
            "escalation_required": escalation_required(
                item["severity"], item["quantity"], item["threshold"]),
        })
        return self.enrich(updated)

    def register_revision(self, item_id: int, payload: Dict[str, Any], actor: str,
                          role: str) -> Dict[str, Any]:
        ensure_role(role, REVISION_ROLES)
        actor = require_text(actor, "actor", 100)
        original = require_number(payload.get("original_value"), "original_value")
        new_value = require_number(payload.get("new_value"), "new_value")
        material_ref = require_text(payload.get("material_ref"), "material_ref", 100)
        expected = payload.get("expected_version")
        if not isinstance(expected, int) or expected < 1:
            raise ValueError("expected_version必须是正整数")
        item = self.repository.get_item(item_id)
        if item["status"] in TERMINAL_STATES:
            raise ConflictError("已关闭事件不能登记修订")
        if expected != item["version"]:
            raise ConflictError("版本冲突，请刷新后重试")
        if abs(original - item["quantity"]) > 1e-9:
            raise ConflictError("原值与当前读数不一致，请刷新后重试")
        if self.repository.pending_revision_count(item_id) > 0:
            raise ConflictError("存在待复核修订，请先处理")
        revision = self.repository.create_revision(item_id, original, new_value,
                                                   material_ref, expected, actor)
        self.repository.append_audit("revision_register", ENTITY, item_id, actor, {
            "revision_id": revision["id"], "batch": revision["batch"],
            "original_value": original, "new_value": new_value,
            "material_ref": material_ref,
        })
        return revision

    def apply_revision(self, item_id: int, revision_id: int, actor: str,
                       role: str) -> Dict[str, Any]:
        ensure_role(role, REVISION_REVIEW_ROLES)
        actor = require_text(actor, "actor", 100)
        revision = self.repository.get_item_revision(item_id, revision_id)
        if revision["status"] != "pending":
            raise ConflictError("修订已处理，请刷新后重试")
        item = self.repository.get_item(item_id)
        if item["status"] in TERMINAL_STATES:
            raise ConflictError("已关闭事件不能应用修订")
        if abs(item["quantity"] - revision["original_value"]) > 1e-9:
            raise ConflictError("当前读数与修订原值不一致，请刷新后重试")
        old_conclusion = dict(conclusion_snapshot(
            item["severity"], item["quantity"], item["threshold"]), superseded=True)
        new_conclusion = conclusion_snapshot(
            item["severity"], revision["new_value"], item["threshold"])
        rejudge = should_rejudge_open_records(
            item["severity"], revision["new_value"], item["threshold"])
        revision, closed_ids = self.repository.apply_revision(
            item_id, revision_id, revision["new_value"], old_conclusion,
            new_conclusion, rejudge, actor)
        self.repository.append_audit("revision_apply", ENTITY, item_id, actor, {
            "revision_id": revision_id, "batch": revision["batch"],
            "original_value": revision["original_value"],
            "new_value": revision["new_value"],
            "material_ref": revision["material_ref"],
            "old_conclusion": old_conclusion, "new_conclusion": new_conclusion,
            "closed_record_ids": closed_ids,
        })
        return {"revision": revision,
                "item": self.enrich(self.repository.get_item(item_id)),
                "closed_record_ids": closed_ids}

    def reject_revision(self, item_id: int, revision_id: int, payload: Dict[str, Any],
                        actor: str, role: str) -> Dict[str, Any]:
        ensure_role(role, REVISION_REVIEW_ROLES)
        actor = require_text(actor, "actor", 100)
        note = payload.get("reason")
        if note is not None:
            note = require_text(note, "reason", 500)
        revision = self.repository.reject_revision(item_id, revision_id, note, actor)
        self.repository.append_audit("revision_reject", ENTITY, item_id, actor, {
            "revision_id": revision_id, "batch": revision["batch"], "reason": note,
        })
        return revision

    def list_revisions(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_revisions(item_id)

    def get_item(self, item_id: int, role: str) -> Dict[str, Any]:
        self._view(role)
        return self.enrich(self.repository.get_item(item_id))

    def list_items(self, role: str, status: Optional[str] = None) -> list:
        self._view(role)
        return [self.enrich(item) for item in self.repository.list_items(status)]

    def list_records(self, item_id: int, role: str) -> list:
        self._view(role)
        return self.repository.list_records(item_id)

    def audit(self, role: str, item_id: Optional[int] = None) -> list:
        ensure_role(role, AUDIT_ROLES)
        return self.repository.list_audit(item_id)

    def enrich(self, item: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(item)
        result["priority"] = priority_score(
            item["severity"], item["quantity"], item["threshold"])
        result["deadline_hours"] = response_deadline_hours(
            item["severity"], item["quantity"], item["threshold"])
        result["escalation_required"] = escalation_required(
            item["severity"], item["quantity"], item["threshold"])
        stats = self.repository.revision_stats(item["id"])
        open_records = self.repository.open_record_count(item["id"])
        result["revision_batch"] = stats["batch"]
        result["pending_revisions"] = stats["pending"]
        blockers = completion_blockers("closed", open_records, stats["pending"])
        if item["status"] in TERMINAL_STATES:
            blockers = blockers + ["事件已关闭"]
        elif not can_transition(item["status"], "closed"):
            blockers = blockers + [f"当前状态{item['status']}不能关闭"]
        result["close_blockers"] = blockers
        result["can_close"] = not blockers
        return result
