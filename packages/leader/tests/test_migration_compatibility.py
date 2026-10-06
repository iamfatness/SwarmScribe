"""Every migration must leave the previous release working.

During a rolling upgrade the migration runs first and the previous release's replicas keep
serving on the new schema until they are replaced (`/readyz` stays 200 on a newer schema:
api/health.py). That is only safe when the new schema still holds everything the previous
release uses. So a change is made in two releases: expand (add the new column or table, keep
the old), then, a release later, contract (remove the old).

This guard reads the migrations as text; it needs no database. It refuses a step in
`upgrade()` that takes something away, unless a comment directly above it (or on its line)
says why the previous release no longer needs it:

    # contract: 0.3.0 stopped reading jobs.legacy_state (it reads jobs.state since 0.2.0)
    op.drop_column("jobs", "legacy_state")

It is a tripwire, not a proof. It does not see a new constraint that the previous release's
writes break, a changed meaning of a value, or SQL built at run time; running the previous
release's tests against the new schema is the real check (leader chart follow-up F4).
"""

import ast
import io
import re
import textwrap
import tokenize
from pathlib import Path

import pytest
from swarmscribe_leader.db.migrate import MIGRATIONS

MARKER = re.compile(r"^#\s*contract:\s*\S.{9,}$")
ALWAYS = {
    "drop_column": "drops a column",
    "drop_table": "drops a table",
    "rename_table": "renames a table",
    "batch_alter_table": "alters a table in a batch, which this guard cannot read",
}
# alter_column arguments that change nothing the previous release depends on.
HARMLESS_ALTERATIONS = {"server_default", "existing_server_default", "comment", "schema"}
RAW_SQL = (
    (re.compile(r"\bDROP\s+(TABLE|COLUMN)\b", re.I), "drops a table or column in SQL"),
    (re.compile(r"\bRENAME\b", re.I), "renames something in SQL"),
    (
        re.compile(r"\bALTER\s+COLUMN\b[^;]*\b(TYPE|SET\s+NOT\s+NULL)\b", re.I | re.S),
        "changes a column's type or makes it required in SQL",
    ),
)


def _is_false(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


def _keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg}


def _what_it_takes_away(call: ast.Call) -> str | None:
    name = call.func.attr if isinstance(call.func, ast.Attribute) else None
    if name in ALWAYS:
        return ALWAYS[name]
    keywords = _keywords(call)
    if name == "alter_column":
        if "new_column_name" in keywords:
            return "renames a column"
        if "type_" in keywords:
            return "changes a column's type"
        if _is_false(keywords.get("nullable")):
            return "makes a column required"
        named = set(keywords) - {key for key in keywords if key.startswith("existing_")}
        if not named <= HARMLESS_ALTERATIONS | {"nullable"}:
            return "alters a column in a way this guard does not know"
    if name == "add_column":
        for argument in call.args:
            if isinstance(argument, ast.Call) and _is_false(_keywords(argument).get("nullable")):
                if "server_default" not in _keywords(argument):
                    return (
                        "adds a required column without a default (the previous release "
                        "does not fill it)"
                    )
    return None


def incompatibilities(source: str, name: str) -> list[str]:
    """The steps of a migration that would break the previous release and are not marked as
    a deliberate contraction. Everything outside `downgrade()` is read."""
    lines = source.splitlines()
    comments = {
        token.start[0]: token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }

    def marked(first: int, last: int) -> bool:
        if any(MARKER.match(comments.get(line, "")) for line in range(first, last + 1)):
            return True
        line = first - 1
        while line >= 1 and lines[line - 1].lstrip().startswith("#"):
            if MARKER.match(comments[line]):
                return True
            line -= 1
        return False

    tree = ast.parse(source)
    statements: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == "downgrade":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.stmt) and not isinstance(
                inner, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ):
                statements.append(inner)

    found: dict[tuple[int, str], None] = {}
    for statement in statements:
        # Only what belongs to this statement itself, not to statements nested in it.
        own = [statement]
        for child in ast.iter_child_nodes(statement):
            if not isinstance(child, ast.stmt):
                own.extend(ast.walk(child))
        reasons = []
        for node in own:
            if isinstance(node, ast.Call) and (reason := _what_it_takes_away(node)):
                reasons.append(reason)
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                reasons.extend(reason for pattern, reason in RAW_SQL if pattern.search(node.value))
        last = statement.end_lineno or statement.lineno
        if reasons and not marked(statement.lineno, last):
            for reason in reasons:
                found[(statement.lineno, reason)] = None
    return [
        f"{name}:{line}: {reason}. The previous release's replicas serve on this schema "
        "during a rolling upgrade: remove things a release after nothing uses them, and "
        "say so above the step with `# contract: <why the previous release no longer "
        "needs it>`"
        for line, reason in sorted(found)
    ]


def _migration(upgrade_body: str, downgrade_body: str = "pass") -> str:
    return (
        "import sqlalchemy as sa\nfrom alembic import op\n\n\n"
        "def upgrade() -> None:\n"
        + textwrap.indent(textwrap.dedent(upgrade_body).strip("\n"), "    ")
        + "\n\n\ndef downgrade() -> None:\n"
        + textwrap.indent(textwrap.dedent(downgrade_body).strip("\n"), "    ")
        + "\n"
    )


def test_no_migration_takes_away_what_the_previous_release_uses():
    versions = sorted((MIGRATIONS / "versions").glob("*.py"))
    assert len(versions) >= 6
    problems = [
        problem
        for path in versions
        for problem in incompatibilities(path.read_text(encoding="utf-8"), path.name)
    ]
    assert problems == [], "\n".join(problems)


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        ('op.drop_column("jobs", "priority")', "drops a column"),
        ('op.drop_table("job_results")', "drops a table"),
        ('op.rename_table("jobs", "work")', "renames a table"),
        ('op.alter_column("jobs", "pool", new_column_name="queue")', "renames a column"),
        ('op.alter_column("jobs", "attempts", type_=sa.SmallInteger())', "changes a column's type"),
        ('op.alter_column("jobs", "cancelled_by", nullable=False)', "makes a column required"),
        ('op.alter_column("jobs", "pool", existing_type=sa.Text(), unknown=1)', "does not know"),
        ('op.add_column("jobs", sa.Column("owner", sa.Text(), nullable=False))', "not fill"),
        ('op.execute("ALTER TABLE jobs DROP COLUMN priority")', "in SQL"),
        ('op.execute("alter table jobs rename column pool to queue")', "renames something in SQL"),
        ('op.execute(sa.text("ALTER TABLE jobs RENAME TO work"))', "renames something in SQL"),
        ('op.execute("ALTER TABLE jobs ALTER COLUMN pool SET NOT NULL")', "makes it required"),
        ('op.execute("ALTER TABLE jobs ALTER COLUMN attempts TYPE smallint")', "column's type"),
        ('op.get_bind().execute(sa.text("DROP TABLE job_results"))', "in SQL"),
        ('with op.batch_alter_table("jobs") as b:\n    b.add_column(sa.Column("x"))', "batch"),
        ('if True:\n    op.drop_column("jobs", "priority")', "drops a column"),
    ],
)
def test_a_step_that_takes_something_away_is_refused(step, reason):
    problems = incompatibilities(_migration(step), "0007_x.py")
    assert problems, step
    assert all("0007_x.py:" in problem and "# contract:" in problem for problem in problems)
    assert any(reason in problem for problem in problems), problems


@pytest.mark.parametrize(
    "step",
    [
        'op.add_column("jobs", sa.Column("owner", sa.Text(), nullable=True))',
        'op.add_column("jobs", sa.Column("n", sa.Integer(), nullable=False, server_default="0"))',
        'op.create_table("things", sa.Column("id", sa.Uuid(), primary_key=True))',
        'op.create_index("ix_jobs_owner", "jobs", ["owner"])',
        'op.drop_index("ix_jobs_claim", table_name="jobs")',
        'op.alter_column("jobs", "cancelled_by", nullable=True, existing_type=sa.Text())',
        'op.alter_column("jobs", "pool", server_default="default")',
        "op.execute(\"UPDATE jobs SET pool = 'default' WHERE pool IS NULL\")",
        'op.execute("DROP INDEX ix_jobs_claim")',
    ],
)
def test_a_step_that_only_adds_is_accepted(step):
    assert incompatibilities(_migration(step), "0007_x.py") == []


def test_what_downgrade_removes_is_not_this_guards_business():
    source = _migration("pass", 'op.drop_column("jobs", "owner")\nop.drop_table("things")')
    assert incompatibilities(source, "0007_x.py") == []


def test_a_contraction_is_accepted_when_it_says_why():
    above = """
        # contract: 0.3.0 no longer reads jobs.priority (it reads jobs.rank since 0.2.0)
        op.drop_column("jobs", "priority")
    """
    above_with_more = """
        # contract: 0.3.0 no longer reads jobs.priority; it reads jobs.rank since 0.2.0,
        # which 0006 filled for every existing row.
        op.drop_column(
            "jobs",
            "priority",
        )
    """
    same_line = 'op.drop_table("old")  # contract: nothing has used it since 0.2.0'
    for body in (above, above_with_more, same_line):
        assert incompatibilities(_migration(body), "0007_x.py") == [], body


@pytest.mark.parametrize(
    "body",
    [
        # no reason given
        '# contract:\nop.drop_column("jobs", "priority")',
        '# contract: ok\nop.drop_column("jobs", "priority")',
        # the reason is for another step
        '# contract: jobs.priority is unused since 0.2.0\nop.drop_column("jobs", "priority")\n'
        'op.drop_table("job_results")',
        # a blank line: the comment no longer belongs to the step
        '# contract: jobs.priority is unused since 0.2.0\n\nop.drop_column("jobs", "priority")',
        # not a comment
        '"contract: jobs.priority is unused since 0.2.0"\nop.drop_column("jobs", "priority")',
    ],
)
def test_a_contraction_without_its_own_reason_is_refused(body):
    assert len(incompatibilities(_migration(body), "0007_x.py")) == 1


def test_the_template_tells_a_migrations_author_the_rule():
    template = (MIGRATIONS / "script.py.mako").read_text(encoding="utf-8")
    assert "previous release" in template
    assert "# contract:" in template
    assert "test_migration_compatibility" in template
    assert Path(__file__).name == "test_migration_compatibility.py"
