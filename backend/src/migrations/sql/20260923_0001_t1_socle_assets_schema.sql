-- T1 — Socle assets + schema (spec #12, ticket #13).
-- Phase expand : table brand_kits + champs clip nullables, aucun comportement change.
--
-- UP (applique par src/database.py:init_db, ordre alphabetique) :
--   CREATE TABLE brand_kits + ALTER TABLE generated_clips ADD COLUMN ...
-- DOWN (rollback manuel sur base fraiche, sans point-virgule pour le split init_db) :
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS brand_kit_id
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS preset
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS template
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS selected_hook_variant
--   ALTER TABLE generated_clips DROP COLUMN IF EXISTS hook_variants
--   DROP TABLE IF EXISTS brand_kits

CREATE TABLE IF NOT EXISTS brand_kits (
    id VARCHAR(36) PRIMARY KEY DEFAULT uuid_generate_v4()::text,
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    logo_path VARCHAR(500),
    font_family VARCHAR(100),
    primary_color VARCHAR(7),
    secondary_color VARCHAR(7),
    cta_text VARCHAR(200),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS hook_title VARCHAR(200);
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS hook_variants TEXT;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS selected_hook_variant INTEGER;
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS template VARCHAR(50);
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS preset VARCHAR(20);
ALTER TABLE generated_clips ADD COLUMN IF NOT EXISTS brand_kit_id VARCHAR(36) REFERENCES brand_kits(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_brand_kits_user_id ON brand_kits(user_id);
CREATE INDEX IF NOT EXISTS idx_generated_clips_brand_kit_id ON generated_clips(brand_kit_id);
