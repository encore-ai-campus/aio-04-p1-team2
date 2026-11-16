"""사진 분석→담기→완료 화면 회귀. 네트워크·운영 DB를 사용하지 않는다."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from services import auth, db
from services.api import ApiError

ROOT = Path(__file__).resolve().parents[1]
RESULT = {"id": "sp_test", "name": "검증 생물", "emoji": "🐚", "tone": "aqua", "confidence": "high",
          "safety": {"level": "caution", "reason": "눈으로 관찰해요"}, "protected": False, "summary": "시험 결과",
          "info": {"habitat": "바다", "food": "먹이", "fun_fact": "특징"}, "candidates": [], "source": "example", "photo_id": ""}


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.add = Mock(return_value={"new_species": True, "total_species": 1, "count": 1, "post_id": None})
        self.aquarium = Mock(return_value=[])
        for target, replacement in (("services.auth.current_user", lambda: {"id": "test-user"}),
                                    ("services.db.aquarium", self.aquarium),
                                    ("services.db.add_observation", self.add),
                                    ("services.db.remaining", lambda user: (3, 3)),
                                    ("services.db.settings", lambda: {"formats": ["jpg", "png"], "max_mb": 10}),
                                    ("components.nav.link", lambda *args, **kw: None),
                                    ("components.location_map.choose_place", lambda **kw: "")):
            patcher = patch(target, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.at = AppTest.from_file(str(ROOT / "views/identify.py"), default_timeout=10)
        self.at.session_state["identify_step"] = "save"
        self.at.session_state["identify_result"] = dict(RESULT)

    def save(self):
        self.at.run()
        self.assertFalse(self.at.exception)
        self.at.button(key="do_save").click().run()

    def test_success_replaces_form_with_completion(self):
        self.save()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state["identify_step"], "done")
        self.assertFalse(any(button.key == "do_save" for button in self.at.button))
        self.assertTrue(any(button.label == "수족관 보기" for button in self.at.button))
        self.add.assert_called_once()

    def test_success_does_not_query_aquarium_again(self):
        self.aquarium.side_effect = [[], [], ApiError(503, "목록 조회 실패")]
        self.save()
        self.assertFalse(self.at.exception)
        self.assertTrue(any(button.label == "수족관 보기" for button in self.at.button))
        self.assertEqual(self.aquarium.call_count, 2)

    def test_failed_save_stays_on_form(self):
        self.add.side_effect = ApiError(503, "저장 실패 시험")
        self.save()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state["identify_step"], "save")
        self.assertTrue(any(error.value == "저장 실패 시험" for error in self.at.error))
        self.assertTrue(any(button.key == "do_save" for button in self.at.button))

    def test_completion_reruns_never_submit_again(self):
        self.save()
        self.at.run()
        self.at.run()
        self.assertFalse(self.at.exception)
        self.add.assert_called_once()
        self.assertEqual(self.aquarium.call_count, 2)
        self.assertTrue(self.at.success)
        self.assertEqual(self.at.session_state["identify_step"], "done")

    def test_previously_saved_session_skips_form(self):
        self.at.session_state["identify_done"] = {**self.add.return_value, "name": RESULT["name"]}
        self.aquarium.side_effect = AssertionError("완료 뒤 목록 조회 금지")
        self.at.run()
        self.assertFalse(self.at.exception)
        self.add.assert_not_called()
        self.aquarium.assert_not_called()
        self.assertTrue(self.at.success)

    def test_existing_species_and_shared_post_completion(self):
        self.add.return_value = {"new_species": False, "total_species": 2, "count": 3, "post_id": "test-post"}
        self.save()
        self.assertFalse(self.at.exception)
        self.assertTrue(any(button.label == "공유한 글 보기" for button in self.at.button))
        self.assertTrue(any("3번째" in item.value for item in self.at.markdown))

    def test_start_another_analysis_clears_completion(self):
        self.save()
        next(button for button in self.at.button if button.label == "다른 생물 알아보기").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state["identify_step"], "upload")
        self.assertFalse(any(button.label == "수족관 보기" for button in self.at.button))
        self.add.assert_called_once()

    def test_view_aquarium_navigates_and_clears_completion(self):
        self.save()
        with patch("components.nav.go") as go:
            next(button for button in self.at.button if button.label == "수족관 보기").click().run()
        self.assertFalse(self.at.exception)
        go.assert_called_once_with("aquarium")
        self.assertNotIn("identify_done", self.at.session_state)

    def test_failed_save_can_retry_once(self):
        saved = dict(self.add.return_value)
        self.add.side_effect = [ApiError(503, "저장 실패 시험"), saved]
        self.save()
        self.at.button(key="do_save").click().run()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.at.session_state["identify_step"], "done")
        self.assertEqual(self.add.call_count, 2)

    def test_uploaded_photo_proof_is_forwarded_to_save(self):
        uploaded = {**RESULT, "photo_id": "ph_testphoto1", "photo_claim": "test-proof", "source": "gemini"}
        self.at.session_state["identify_result"] = uploaded
        self.save()
        self.assertFalse(self.at.exception)
        self.assertEqual(self.add.call_args.args[1]["photo_id"], uploaded["photo_id"])
        self.assertEqual(self.add.call_args.args[1]["photo_claim"], uploaded["photo_claim"])
        self.assertEqual(self.at.session_state["identify_step"], "done")


if __name__ == "__main__":
    unittest.main()
