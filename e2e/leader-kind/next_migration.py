"""Run while building next.Dockerfile (TEST ONLY): adds one migration after the image's head.

It changes nothing but the revision. Written at build time, not kept in the repository,
because its `down_revision` must be whatever the head is in the image under test."""

from swarmscribe_leader.db.migrate import MIGRATIONS, head_revision

REVISION = "9999"  # e2e/leader-kind/run_e2e.py: NEXT_REVISION

TEMPLATE = '''"""The kind test's stand-in for the next version's migration: the revision only."""

revision = "{revision}"
down_revision = "{head}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def main() -> None:
    head = head_revision()
    if head == REVISION:
        raise SystemExit("this image already holds the test's migration")
    target = MIGRATIONS / "versions" / f"{REVISION}_e2e_next_version.py"
    target.write_text(TEMPLATE.format(revision=REVISION, head=head), encoding="utf-8")
    if head_revision() != REVISION:
        raise SystemExit(f"the head is {head_revision()}, not {REVISION}")
    print(f"added migration {REVISION} after {head}")


if __name__ == "__main__":
    main()
