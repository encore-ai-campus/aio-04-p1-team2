"""차유진 담당: 게시판 드래그 순서·상태 저장 회귀 검사. 실제 DB는 사용하지 않아요."""
# 2026.11.13 - yujin -"드래그 변경 저장, 실패 시 편집 유지, 잘못된 순서와 동시 변경을 검사해요."
import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from services.api import ApiError


class BoardOrderTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"id": bid, "order": i, "status": "운영 중"} for i, bid in enumerate("abc", 1)]
        self.base = {"ids": list("abc"), "status": {bid: "운영 중" for bid in "abc"}}
        self.state = {"boards_table_base": deepcopy(self.base)}
        self.save = Mock()
        source = ast.parse((Path(__file__).resolve().parents[1] / "views/admin/boards.py").read_text(encoding="utf-8"))
        funcs = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in ("save_table", "table_changed")]
        self.env = {"S": self.state, "db": SimpleNamespace(boards=lambda **kw: deepcopy(self.rows)),
                    "cd": SimpleNamespace(save_order=self.save, STATUSES=("준비 중", "운영 중", "일시 중지", "보관")),
                    "ApiError": ApiError}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), "views/admin/boards.py", "exec"), self.env)

    def change(self, model):
        self.state["component"] = SimpleNamespace(model=model)
        self.env["table_changed"]("component")

    def test_order_and_status_save_together_then_reset(self):
        self.change({"ids": list("cab"), "status": {"a": "운영 중", "b": "운영 중", "c": "보관"}})
        self.env["save_table"]()
        self.save.assert_called_once_with(list("cab"), {"c": "보관"})
        self.assertNotIn("boards_table_pending", self.state)
        self.assertEqual(self.state["boards_tv"], 1)

    def test_failed_save_preserves_pending_changes(self):
        model = {"ids": list("bac"), "status": dict(self.base["status"])}
        self.change(model)
        self.save.side_effect = ApiError(503, "서버 연결 오류")
        self.env["save_table"]()
        self.assertEqual(self.state["boards_table_pending"], model)
        self.assertNotIn("boards_tv", self.state)
        self.assertEqual(self.state["boards_toast"], "서버 연결 오류")

    def test_no_changes_and_reverted_changes_do_not_save(self):
        self.env["save_table"]()
        self.change(deepcopy(self.base))
        self.env["save_table"]()
        self.save.assert_not_called()

    # 2026.11.13 - yujin -"저장 전 조회 실패 시에도 드래그 편집이 사라지지 않는지 검사해요."
    def test_failed_refresh_preserves_pending_changes(self):
        model = {"ids": list("bac"), "status": dict(self.base["status"])}
        self.change(model)
        self.env["db"].boards = Mock(side_effect=ApiError(503, "목록 조회 오류"))
        self.env["save_table"]()
        self.save.assert_not_called()
        self.assertEqual(self.state["boards_table_pending"], model)
        self.assertEqual(self.state["boards_toast"], "목록 조회 오류")

    def test_invalid_component_changes_are_rejected(self):
        for model in (None, {}, {"ids": ["a", "a", "c"], "status": self.base["status"]},
                      {"ids": ["a", "b", {}], "status": self.base["status"]},
                      {"ids": list("abc"), "status": {"a": "invalid", "b": "운영 중", "c": "운영 중"}},
                      {"ids": list("abc"), "status": {"a": "운영 중"}}):
            with self.subTest(model=model):
                self.change(model)
                self.assertNotIn("boards_table_pending", self.state)

    def test_concurrent_board_removal_prevents_stale_save(self):
        self.change({"ids": list("bac"), "status": dict(self.base["status"])})
        self.rows.pop()
        self.env["save_table"]()
        self.save.assert_not_called()
        self.assertIn("목록이 바뀌었어요", self.state["boards_toast"])


if __name__ == "__main__":
    unittest.main()
