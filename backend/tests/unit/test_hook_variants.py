"""T3 — 3 variantes hook par clip : normalisation + completion deterministe."""

from src.caption_templates import get_template
from src.media.captions import build_hook_title_ass
from src.hook_variants import (
    build_hook_variants,
    resolve_segment_hook,
    sanitize_hook_title,
    select_hook_title,
)


def test_build_hook_variants_returns_three_sanitized_unique_titles():
    variants = build_hook_variants(
        hook_title="  \"L'erreur a 40000 euros\"  ",
        segment_text="On parle d'une erreur qui coute quarante mille euros aux debutants",
        hook_type="statement",
    )
    assert len(variants) == 3
    assert len(set(variants)) == 3
    for variant in variants:
        assert variant == sanitize_hook_title(variant)
        assert len(variant.split()) <= 10
        assert len(variant) <= 64


def test_build_hook_variants_dedupes_and_completes_to_three():
    variants = build_hook_variants(
        hook_title="Le secret des pros",
        segment_text="Le secret des pros pour progresser vite sans materiel cher",
        hook_type="statement",
        provided_variants=["Le secret des pros", "le secret des pros ", ""],
    )
    assert len(variants) == 3
    assert len(set(variants)) == 3
    assert variants[0] == "Le secret des pros"


def test_build_hook_variants_without_title_falls_back_to_segment_text():
    variants = build_hook_variants(
        hook_title=None,
        segment_text="Comment doubler ton audience en trente jours avec des clips courts",
        hook_type="question",
    )
    assert len(variants) == 3
    assert len(set(variants)) == 3
    for variant in variants:
        assert variant


def test_build_hook_variants_keeps_valid_provided_variants_first():
    variants = build_hook_variants(
        hook_title="Titre principal",
        segment_text="Un texte de segment assez long pour deriver des variantes",
        hook_type="contrast",
        provided_variants=["Variante avant apres", "3 erreurs a eviter"],
    )
    assert variants[0] == "Titre principal"
    assert variants[1] == "Variante avant apres"
    assert variants[2] == "3 erreurs a eviter"


def test_select_hook_title_preserves_custom_title_until_a_variant_is_selected():
    variants = [" Variante A ", "Variante B", "Variante C"]

    assert select_hook_title(variants, 1, "Variante A") == "Variante B"
    assert select_hook_title(variants, None, "Titre libre") == "Titre libre"
    assert select_hook_title(variants, 9, "Titre libre") == "Titre libre"


def test_resolve_segment_hook_keeps_selection_and_custom_override():
    variants, title = resolve_segment_hook(
        {
            "hook_title": "Titre libre",
            "hook_variants": ["A", "B", "C"],
            "selected_hook_variant": 2,
            "text": "Un segment",
        }
    )
    assert variants == ["A", "B", "C"]
    assert title == "C"

    _, custom_title = resolve_segment_hook(
        {
            "hook_title": "Titre libre",
            "hook_variants": ["A", "B", "C"],
            "text": "Un segment",
        }
    )
    assert custom_title == "Titre libre"


def test_hook_title_kinetic_events_reveal_characters_during_hook_window():
    _, events = build_hook_title_ass(
        "Le titre",
        get_template("default"),
        1080,
        1920,
        15.0,
        "Arial",
        54,
    )

    assert len(events) > 1
    assert events[0].startswith("Dialogue: 1,0:00:00.12")
    assert ",0:00:04.00,Hook" in events[-1]
    assert all(character in events[-1] for character in "Letitre")

    _, highlighted = build_hook_title_ass(
        "Le résultat",
        get_template("default"),
        1080,
        1920,
        15.0,
        "Arial",
        54,
    )
    assert any("0000E0FF" in event for event in highlighted)
