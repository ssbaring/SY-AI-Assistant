"""Alembic 마이그레이션 자체를 검증한다.

migrated_schema fixture(세션 범위, weighing/conftest.py)가 이미 head까지
올려 두므로, 여기서는 그 결과물(테이블·트리거)과 왕복(upgrade->downgrade->
upgrade)이 안전한지만 확인한다.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic import command

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _config() -> Config:
    return Config(str(ALEMBIC_INI))


def test_single_head_revision():
    script = ScriptDirectory.from_config(_config())
    heads = script.get_heads()
    assert len(heads) == 1


def test_upgrade_creates_expected_tables(raw_conn):
    rows = raw_conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name LIKE 'weighing%'"
    ).fetchall()
    names = {row[0] for row in rows}
    assert names == {
        "weighing_records",
        "weighing_record_history",
        "weighing_ticket_sequences",
    }


def test_upgrade_creates_expected_triggers(raw_conn):
    rows = raw_conn.execute(
        "SELECT event_object_table, trigger_name FROM information_schema.triggers "
        "WHERE trigger_schema = 'public' ORDER BY 1, 2"
    ).fetchall()
    names = {row[1] for row in rows}
    assert {
        "trg_weighing_records_assign_ticket_no",
        "trg_weighing_records_set_updated_at",
        "trg_weighing_records_immutable_when_canceled",
        "trg_weighing_records_no_delete",
        "trg_weighing_records_history",
        "trg_weighing_record_history_no_delete",
        "trg_weighing_record_history_no_update",
    } <= names


def test_migration_does_not_import_application_code():
    """마이그레이션이 app 코드를 import하면 안 된다(요구사항 2).

    미래에 app.services.weighing_limits의 값이 바뀌어도 이 리비전이 만드는
    스키마는 항상 같아야 하므로, 이 파일은 import문 자체로 검사한다.
    """

    versions_dir = Path(__file__).resolve().parents[2] / "alembic" / "versions"
    migration_files = list(versions_dir.glob("*.py"))
    assert migration_files, "마이그레이션 파일을 찾지 못했습니다."
    for path in migration_files:
        source = path.read_text(encoding="utf-8")
        assert "import app" not in source, f"{path.name}이(가) app 코드를 import합니다."
        assert "from app" not in source, f"{path.name}이(가) app 코드를 import합니다."


def test_migration_weight_limits_match_current_runtime_constants(raw_conn):
    """마이그레이션에 고정된 값과 app.services.weighing_limits의 현재 값을 비교한다.

    두 값은 지금은 같아야 한다. 나중에 weighing_limits.py의 값을 바꾸면 이
    테스트가 실패해, "새 마이그레이션을 추가해야 한다"는 신호를 준다(요구사항 2).
    """

    from app.services.weighing_limits import MAX_WEIGHT_KG, MIN_WEIGHT_KG

    definition = raw_conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'ck_weighing_records_gross_weight_range'"
    ).fetchone()[0]
    assert f"{MIN_WEIGHT_KG}" in definition
    assert f"{MAX_WEIGHT_KG}" in definition


def test_downgrade_then_upgrade_round_trip(raw_conn):
    """downgrade(base) -> upgrade(head)가 오류 없이 왕복되는지 확인한다.

    끝나면 반드시 head로 복원한다(다른 테스트가 이어서 스키마를 쓰기 때문).
    """

    cfg = _config()
    try:
        command.downgrade(cfg, "base")

        tables = raw_conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name LIKE 'weighing%'"
        ).fetchone()[0]
        assert tables == 0

        command.upgrade(cfg, "head")

        tables_after = raw_conn.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name LIKE 'weighing%'"
        ).fetchone()[0]
        assert tables_after == 3
    finally:
        command.upgrade(cfg, "head")
