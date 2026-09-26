from __future__ import annotations

from typing import Any, Dict, Optional

from .domain import (ConflictError, NotFoundError, ValidationError, ensure_role,
                     normalize_severity, require_number, require_text)
from .repository import Repository
from .rules import (AUDIT_ROLES, CREATE_ROLES, ENTITY, RECORD_ROLES, REVISION_ROLES,
                    TERMINAL_STATES, TITLE, VIEW_ROLES, can_transition,
                    completion_blockers, escalation_required, priority_score,
                    response_deadline_hours, role_for_transition,
                    validate_revisable_field, validate_revision_value,
                    validate_transition)


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
        item = self.repository.get_item(item_id)
        if item["status"] in TERMINAL_STATES:
            raise ConflictError("事件已关闭，不能登记修订")
        field = validate_revisable_field(payload.get("field"))
        expected_version = payload.get("expected_version")
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValidationError("expected_version必须是正整数")
        if expected_version != item["version"]:
            raise ConflictError("版本冲突，请刷新后重试")
        old_value = validate_revision_value(field, payload.get("old_value"))
        new_value = validate_revision_value(field, payload.get("new_value"))
        if item[field] != old_value:
            raise ConflictError("原值与当前读数不一致")
        if new_value == old_value:
            raise ValidationError("新值与原值相同")
        material_ref = require_text(payload.get("material_ref"), "material_ref", 100)
        old_conclusion = self._conclusion(item)
        new_conclusion = self._conclusion(dict(item, **{field: new_value}))
        revision = self.repository.create_revision(
            item_id, field, old_value, new_value, material_ref, expected_version,
            old_conclusion, new_conclusion, actor)
        self.repository.append_audit("revision_register", ENTITY, item_id, actor, {
            "revision_id": revision["id"], "batch": revision["batch"], "field": field,
            "old_value": old_value, "new_value": new_value, "material_ref": material_ref,
        })
        return revision

    def confirm_revision(self, item_id: int, revision_id: int, actor: str,
                         role: str) -> Dict[str, Any]:
        ensure_role(role, REVISION_ROLES)
        actor = require_text(actor, "actor", 100)
        item = self.repository.get_item(item_id)
        if item["status"] in TERMINAL_STATES:
            raise ConflictError("事件已关闭，不能复核修订")
        revision = self.repository.get_revision(revision_id)
        if revision["item_id"] != item_id:
            raise NotFoundError("修订不存在")
        if revision["status"] != "pending":
            raise ConflictError("修订已处理")
        if item[revision["field"]] != revision["old_value"]:
            raise ConflictError("修订基础已变化，请重新登记")
        updated, rejudged = self.repository.apply_revision(
            item_id, revision_id, revision["field"], revision["new_value"],
            revision["batch"], actor)
        self.repository.append_audit("revision_confirm", ENTITY, item_id, actor, {
            "revision_id": revision_id, "batch": revision["batch"],
            "field": revision["field"], "old_value": revision["old_value"],
            "new_value": revision["new_value"], "material_ref": revision["material_ref"],
            "old_conclusion": revision["old_conclusion"],
            "new_conclusion": revision["new_conclusion"], "rejudged_records": rejudged,
        })
        return {"revision": self.repository.get_revision(revision_id),
                "item": self.enrich(updated)}

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

    @staticmethod
    def _conclusion(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "priority": priority_score(item["severity"], item["quantity"], item["threshold"]),
            "deadline_hours": response_deadline_hours(
                item["severity"], item["quantity"], item["threshold"]),
            "escalation_required": escalation_required(
                item["severity"], item["quantity"], item["threshold"]),
        }

    def enrich(self, item: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(item)
        result.update(self._conclusion(item))
        open_records = self.repository.open_record_count(item["id"])
        pending = self.repository.pending_revision_count(item["id"])
        result["revision_batch"] = self.repository.latest_applied_batch(item["id"])
        result["pending_revisions"] = pending
        blockers = completion_blockers("closed", open_records, pending)
        result["can_close"] = can_transition(item["status"], "closed") and not blockers
        result["close_blockers"] = blockers
        return result
