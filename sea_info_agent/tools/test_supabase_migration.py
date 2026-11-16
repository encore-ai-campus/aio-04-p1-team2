"""이전 정책 회귀. 원격 연결 없이 메모리 DB에서 중복·연결 보존을 확인한다."""
import unittest

from sqlalchemy import Boolean, Column, ForeignKey, MetaData, String, Table, create_engine

from tools.migrate_sqlite_to_supabase import compare


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)
        metadata = MetaData()
        self.comments = Table("comment", metadata, Column("id", String, primary_key=True),
                              Column("post_id", String), Column("user_id", String), Column("body", String), Column("deleted", Boolean))
        self.replies = Table("comment_reply", metadata,
                             Column("comment_id", String, ForeignKey("comment.id"), primary_key=True),
                             Column("parent_id", String, ForeignKey("comment.id")))
        metadata.create_all(self.engine)

    def row(self, identity, body="same", deleted=False):
        return {"id": identity, "post_id": "p1", "user_id": "u1", "body": body, "deleted": deleted}

    def test_different_ids_same_comment_is_not_inserted(self):
        with self.engine.begin() as connection:
            connection.execute(self.comments.insert(), self.row("remote"))
            plan, rows = compare(connection, None, {"comment": [self.row("local")]})
            self.assertEqual(plan["comment"]["insert"], 0)
            self.assertEqual(plan["comment"]["natural_matches"], 1)

    def test_repeated_comments_are_matched_one_to_one(self):
        with self.engine.begin() as connection:
            connection.execute(self.comments.insert(), self.row("remote"))
            plan, rows = compare(connection, None, {"comment": [self.row("local1"), self.row("local2")]})
            self.assertEqual(plan["comment"]["insert"], 1)

    def test_different_content_is_preserved(self):
        with self.engine.begin() as connection:
            connection.execute(self.comments.insert(), self.row("remote"))
            plan, rows = compare(connection, None, {"comment": [self.row("local", body="new")]})
            self.assertEqual(plan["comment"]["insert"], 1)

    def test_reply_parent_id_is_mapped(self):
        with self.engine.begin() as connection:
            connection.execute(self.comments.insert(), self.row("remote"))
            source = {"comment": [self.row("local"), self.row("reply", body="reply")],
                      "comment_reply": [{"comment_id": "reply", "parent_id": "local"}]}
            plan, rows = compare(connection, None, source)
            self.assertEqual(rows["comment_reply"][1][0]["parent_id"], "remote")

    def test_exact_id_is_not_matched_again(self):
        with self.engine.begin() as connection:
            connection.execute(self.comments.insert(), self.row("same-id"))
            plan, rows = compare(connection, None, {"comment": [self.row("same-id"), self.row("new-id")]})
            self.assertEqual(plan["comment"]["insert"], 1)

    def test_repeated_plan_is_idempotent(self):
        source = {"comment": [self.row("local")]}
        with self.engine.begin() as connection:
            connection.execute(self.comments.insert(), self.row("remote"))
            first, _ = compare(connection, None, source)
            second, _ = compare(connection, None, source)
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
