from __future__ import annotations
from .domain import ConflictError, ValidationError, normalize_severity, require_number
TITLE='企业排污许可与超标处置'; ENTITY='排污事件'; ID_PREFIX='ED'
SEVERITIES=['normal', 'watch', 'exceedance', 'major']; STATES=['reported', 'assessing', 'remediation', 'inspection', 'closed']; TRANSITIONS={'reported': ['assessing'], 'assessing': ['remediation'], 'remediation': ['inspection'], 'inspection': ['closed'], 'closed': []}; TRANSITION_ROLES={'assessing': ['compliance_officer'], 'remediation': ['operator'], 'inspection': ['compliance_officer'], 'closed': ['director']}
CREATE_ROLES=set(['operator', 'compliance_officer']); RECORD_ROLES=set(['operator', 'compliance_officer']); AUDIT_ROLES=set(['director', 'viewer']); VIEW_ROLES=set(['operator', 'compliance_officer', 'director', 'viewer'])
REVISABLE_FIELDS=set(['quantity', 'severity', 'threshold']); NUMERIC_FIELD_MINIMUM={'quantity': 0.0, 'threshold': 0.000001}; REVISION_ROLES=set(['compliance_officer']); REVISION_STATUSES=['pending', 'applied']
SEVERITY_WEIGHT={'normal': 1.0, 'watch': 3.0, 'exceedance': 6.0, 'major': 9.0}; DEADLINE_HOURS={'normal': 72, 'watch': 24, 'exceedance': 8, 'major': 4}; TERMINAL_STATES=set(['closed'])
def priority_score(severity,quantity=0.0,threshold=1.0,open_records=0):
    if severity not in SEVERITY_WEIGHT: raise ValidationError("unknown severity")
    ratio=quantity/threshold if threshold>0 else 1.0
    return max(0,min(10,int(round(SEVERITY_WEIGHT[severity]+min(4.0,ratio*4.0)+min(3.0,float(open_records))))))
def response_deadline_hours(severity,quantity=0.0,threshold=1.0):
    if severity not in DEADLINE_HOURS: raise ValidationError("unknown severity")
    ratio=quantity/threshold if threshold>0 else 1.0
    return max(1,int(DEADLINE_HOURS[severity]/max(1.0,ratio)))
def escalation_required(severity,quantity=0.0,threshold=1.0):
    return severity==SEVERITIES[-1] or (threshold>0 and quantity>=threshold)
def can_transition(current,target): return target in TRANSITIONS.get(current,[])
def validate_transition(current,target):
    if current not in STATES or target not in STATES: raise ValidationError("未知状态")
    if not can_transition(current,target): raise ConflictError(f"不能从{current}转换到{target}")
def completion_blockers(target,open_records,pending_revisions=0):
    if target not in TERMINAL_STATES: return []
    blockers=[]
    if open_records>0: blockers.append("仍有未关闭事项")
    blockers+=revision_blockers(pending_revisions)
    return blockers
def revision_blockers(pending_revisions): return ["存在待复核修订，请先处理"] if pending_revisions>0 else []
def validate_revisable_field(field):
    if field not in REVISABLE_FIELDS: raise ValidationError("field不允许修订")
    return field
def validate_revision_value(field,value):
    validate_revisable_field(field)
    if field=='severity': return normalize_severity(value)
    return require_number(value,field,NUMERIC_FIELD_MINIMUM[field])
def role_for_transition(target): return set(TRANSITION_ROLES.get(target,[]))
