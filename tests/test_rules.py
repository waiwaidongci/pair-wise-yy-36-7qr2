import unittest
from src import rules
from src.domain import ConflictError, ValidationError
class RulesTest(unittest.TestCase):
    def test_priority_deadline_and_escalation(self):
        low=rules.priority_score(rules.SEVERITIES[0],1,10,0); high=rules.priority_score(rules.SEVERITIES[-1],30,10,3)
        self.assertGreater(high,low); self.assertLessEqual(rules.response_deadline_hours(rules.SEVERITIES[-1],30,10),rules.response_deadline_hours(rules.SEVERITIES[0],1,10))
        self.assertTrue(rules.escalation_required(rules.SEVERITIES[-1],1,10)); self.assertTrue(rules.escalation_required(rules.SEVERITIES[0],10,10))
    def test_transition_guards(self):
        self.assertTrue(rules.can_transition(rules.STATES[0],rules.STATES[1]))
        with self.assertRaises(ConflictError): rules.validate_transition(rules.STATES[0],rules.STATES[-1])
        with self.assertRaises(ValidationError): rules.priority_score("not-a-severity",1,1)
    def test_completion_blockers_include_pending_revisions(self):
        self.assertEqual(rules.completion_blockers(rules.STATES[-1],0),[])
        self.assertEqual(rules.completion_blockers(rules.STATES[0],1,1),[])
        blockers=rules.completion_blockers(rules.STATES[-1],1,1)
        self.assertIn("存在待复核修订，请先处理",blockers); self.assertIn("仍有未关闭事项",blockers)
if __name__=="__main__": unittest.main()
