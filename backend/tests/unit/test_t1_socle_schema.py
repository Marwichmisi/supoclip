"""T1 — Socle assets + schema : champs clip nullables, table brand_kits.

Phase expand : nouveaux champs nuls par defaut, aucun comportement change.
Tests sans DB (metadata SQLAlchemy + fichiers SQL + manifests).
"""

from pathlib import Path

from src.models import Base, BrandKit, GeneratedClip

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
MIGRATION = (
    BACKEND_ROOT
    / "src"
    / "migrations"
    / "sql"
    / "20260923_0001_t1_socle_assets_schema.sql"
)
INIT_SQL = REPO_ROOT / "init.sql"


def _clip_columns():
    return {c.name: c for c in GeneratedClip.__table__.columns}


def test_clip_t1_fields_nullable_default_null():
    cols = _clip_columns()
    for name in (
        "hook_title",
        "hook_variants",
        "selected_hook_variant",
        "template",
        "preset",
        "brand_kit_id",
    ):
        assert name in cols, f"colonne clip manquante : {name}"
        assert cols[name].nullable, f"colonne {name} doit etre nullable"
        # Pas de default serveur : nul par defaut (expand, sans comportement).
        assert cols[name].server_default is None, (
            f"colonne {name} doit etre nulle par defaut"
        )


def test_brand_kits_table_one_kit_per_user():
    assert BrandKit.__tablename__ == "brand_kits"
    cols = {c.name: c for c in BrandKit.__table__.columns}
    for name in (
        "id",
        "user_id",
        "logo_path",
        "font_family",
        "primary_color",
        "secondary_color",
        "cta_text",
    ):
        assert name in cols, f"colonne brand_kits manquante : {name}"
    assert cols["user_id"].nullable is False
    assert cols["user_id"].unique is True
    for name in (
        "logo_path",
        "font_family",
        "primary_color",
        "secondary_color",
        "cta_text",
    ):
        assert cols[name].nullable, f"brand_kits.{name} doit etre nullable"


def test_metadata_creates_all_tables_sqlite():
    # Cohérence ORM : brand_kits se crée (users n'a pas de type PG-only).
    # generated_clips est vérifié via metadata (tasks porte un ARRAY PG-only
    # pré-existant, non créable sur sqlite — hors scope T1).
    import sqlalchemy as sa

    from src.models import User

    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine, tables=[User.__table__, BrandKit.__table__]
    )
    with engine.connect() as conn:
        tables = {
            row[0]
            for row in conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table'"))
        }
    assert "brand_kits" in tables
    assert "users" in tables
    # generated_clips : présent dans la metadata avec FK vers brand_kits.
    assert "generated_clips" in Base.metadata.tables
    fk_targets = {
        fk.target_fullname for fk in GeneratedClip.__table__.foreign_keys
    }
    assert "brand_kits.id" in fk_targets


def test_migration_file_contains_up_and_down():
    assert MIGRATION.exists(), f"migration T1 manquante : {MIGRATION}"
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS brand_kits" in sql
    for col in (
        "hook_variants",
        "selected_hook_variant",
        "template",
        "preset",
        "brand_kit_id",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {col}" in sql, f"UP manquant : {col}"
    # DOWN documente pour rollback manuel (harness actuel = UP only).
    assert "DROP COLUMN IF EXISTS brand_kit_id" in sql
    assert "DROP TABLE IF EXISTS brand_kits" in sql


def test_init_sql_fresh_db_has_t1_schema():
    sql = INIT_SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS brand_kits" in sql
    # brand_kits defini avant generated_clips (FK inline valide sur base fraiche).
    assert sql.index("brand_kits") < sql.index("CREATE TABLE generated_clips")
    for col in ("hook_variants", "selected_hook_variant", "template", "preset", "brand_kit_id"):
        assert col in sql, f"init.sql manque : {col}"
    assert "idx_brand_kits_user_id" in sql
    assert "update_brand_kits_updated_at" in sql


def test_assets_tree_and_manifests_load():
    from src.assets_manifests import load_manifest

    for kind in ("audio", "broll"):
        d = REPO_ROOT / "assets" / kind
        assert d.is_dir(), f"dossier manquant : {d}"
        manifest = d / "manifest.json"
        assert manifest.exists(), f"manifest manquant : {manifest}"
        loaded = load_manifest(manifest)
        assert loaded["kind"] == kind
