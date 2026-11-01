"""G4+G5 S6 browser exception provenance; offline, no network or model."""
import unittest
from spray_schema.agent_loop import run_loop


class TestBrowser:
    def __init__(self, *, fail_once=True, error_cls=TimeoutError):
        self.fail_once=fail_once
        self.error_cls=error_cls
        self.count=0
        self.done=False

    def observe(self):
        return {"title":"Synthetic QA","visible_text":"Pending","controls":[
            {"kind":"button","label":"Primary","enabled":True},
            {"kind":"button","label":"Alternate","enabled":True},
        ]}

    def verify(self,target_text):
        return self.done and target_text=="Completed"

    def click(self,index):
        self.count+=1
        if self.fail_once and self.count==1:
            raise self.error_cls("PRIVATE_FAKE_EXCEPTION_MESSAGE_NEVER_RECORD")
        self.done=True

    def wait(self,milliseconds):
        pass


class Planner:
    def __init__(self,*decisions):
        self.decisions=iter(decisions)
        self.histories=[]

    def choose(self,*,observation,target_text,history):
        self.histories.append(history)
        return next(self.decisions)


class ActionFailureTypeTests(unittest.TestCase):
    def test_failed_click_includes_safe_exception_type(self):
        planner=Planner({"action":"click","index":0},{"action":"click","index":1})
        browser=TestBrowser()
        r=run_loop(browser,planner,target_text="Completed",max_steps=2,
                   require_alternate_on_failure=True)
        self.assertEqual(r.status,"PASS")
        self.assertEqual([e["outcome"] for e in r.trace],["action_failed","executed"])
        self.assertEqual(r.trace[0].get("error_type"),"TimeoutError")
        self.assertEqual(planner.histories[1][0]["error_type"],"TimeoutError")
        self.assertNotIn("PRIVATE_FAKE_EXCEPTION_MESSAGE_NEVER_RECORD",repr(r.trace))

    def test_long_untrusted_exception_class_name_is_redacted(self):
        dangerous="PRIVATE_FAKE_CLASS_"+"x"*100
        unsafe_class=type(dangerous,(Exception,),{})
        planner=Planner({"action":"click","index":0},{"action":"click","index":1})
        r=run_loop(TestBrowser(error_cls=unsafe_class),planner,target_text="Completed",
                   max_steps=2,require_alternate_on_failure=True)
        self.assertEqual(r.status,"PASS")
        self.assertEqual(r.trace[0].get("error_type"),"BrowserActionError")
        self.assertEqual(planner.histories[1][0]["error_type"],"BrowserActionError")
        self.assertNotIn(dangerous,repr(r.trace))
        self.assertNotIn(dangerous,repr(planner.histories))

    def test_unicode_exception_class_name_is_redacted(self):
        unsafe_class=type("가짜비밀Error",(Exception,),{})
        planner=Planner({"action":"click","index":0},{"action":"click","index":1})
        r=run_loop(TestBrowser(error_cls=unsafe_class),planner,target_text="Completed",
                   max_steps=2,require_alternate_on_failure=True)
        self.assertEqual(r.status,"PASS")
        self.assertEqual(r.trace[0].get("error_type"),"BrowserActionError")
        self.assertNotIn("가짜비밀",repr(r.trace))

    def test_untrusted_planner_exception_names_never_enter_trace(self):
        for unsafe_name in ("PRIVATE_FAKE_APIKEYError", "가짜비밀Error",
                            "PRIVATE_FAKE_CLASS_" + "x" * 120):
            with self.subTest(name=unsafe_name):
                secret_cls = type(unsafe_name, (Exception,), {})

                class ExplodingPlanner:
                    def choose(self, **kwargs):
                        raise secret_cls("PRIVATE_FAKE_EXCEPTION_MESSAGE")

                result = run_loop(
                    TestBrowser(fail_once=False), ExplodingPlanner(),
                    target_text="Completed", max_steps=1,
                )
                self.assertEqual((result.status, result.reason),
                                 ("FAIL", "planner_rejected"))
                self.assertEqual(result.trace[0].get("error_type"),
                                 "PlannerDecisionError")
                self.assertNotIn(unsafe_name, repr(result.trace))
                self.assertNotIn("PRIVATE_FAKE_EXCEPTION_MESSAGE", repr(result.trace))

    def test_unknown_short_browser_error_name_not_trusted(self):
        # Even a syntactically valid ASCII identifier ending in Error may
        # contain private data if a dependency dynamically names a class.
        secret_cls = type("PRIVATE_FAKE_APIKEYError", (Exception,), {})
        planner = Planner({"action": "click", "index": 0},
                          {"action": "click", "index": 1})
        result = run_loop(
            TestBrowser(error_cls=secret_cls), planner,
            target_text="Completed", max_steps=2,
            require_alternate_on_failure=True,
        )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.trace[0].get("error_type"), "BrowserActionError")
        self.assertEqual(planner.histories[1][0].get("error_type"),
                         "BrowserActionError")
        self.assertNotIn("PRIVATE_FAKE_APIKEYError", repr(result.trace))

    def test_known_planner_validation_type_stays_diagnostic(self):
        result = run_loop(
            TestBrowser(fail_once=False), Planner({"action": "delete"}),
            target_text="Completed", max_steps=1,
        )
        self.assertEqual((result.status, result.reason),
                         ("FAIL", "planner_rejected"))
        self.assertEqual(result.trace[0].get("error_type"), "ValueError")

    def test_successful_action_has_no_exception_type(self):
        r=run_loop(TestBrowser(fail_once=False),Planner({"action":"click","index":0}),
                   target_text="Completed",max_steps=1)
        self.assertEqual(r.status,"PASS")
        self.assertNotIn("error_type",r.trace[0])

    def test_repeated_failed_action_rejected_before_second_browser_click(self):
        browser=TestBrowser()
        r=run_loop(browser,Planner({"action":"click","index":0},
                                   {"action":"click","index":0}),
                   target_text="Completed",max_steps=2,require_alternate_on_failure=True)
        self.assertEqual((r.status,r.reason),("FAIL","repeated_failed_action"))
        self.assertEqual(browser.count,1)
        self.assertEqual(r.trace[0]["outcome"],"action_failed")


if __name__=="__main__":unittest.main()
