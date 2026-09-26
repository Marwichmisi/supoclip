"""T4 — Sound design local : le plan de cues (spec #12, ticket #16).

Le plan est une fonction pure : timecodes des SFX sur la timeline de sortie du
clip, sans aucun I/O. Les whoosh tombent sur chaque cut interne (projete comme
les captions, via le meme crossfade), le pop marque le mot cle du hook, le rise
precede la punchline.
"""

from src.media.sound import build_sound_plan


def test_whoosh_lands_on_every_cut_projected_on_the_output_timeline():
    plan = build_sound_plan(
        keep_ranges=[(0.0, 4.0), (10.0, 14.0), (20.0, 24.0)],
        duration=11.56,
        words=(),
        has_hook=False,
    )

    whooshes = [cue.at for cue in plan.cues if cue.kind == "whoosh"]

    # 3 ranges de 4s => crossfade 0.22s (min(0.22, 4 * 0.5)). La timeline de
    # sortie se raccourcit donc de 0.22s a chaque jonction, comme les captions :
    # 4.0 - 0.22 = 3.78 puis 8.0 - 0.44 = 7.56.
    assert whooshes == [3.78, 7.56]


def test_a_single_kept_range_has_no_cut_to_mark():
    plan = build_sound_plan(
        keep_ranges=[(12.0, 42.0)],
        duration=30.0,
        words=(),
        has_hook=False,
    )

    assert [cue.kind for cue in plan.cues] == []


def test_pop_anchors_on_the_hook_key_word():
    plan = build_sound_plan(
        keep_ranges=[(0.0, 30.0)],
        duration=30.0,
        words=[
            {"text": "ecoute", "start": 0.40, "end": 0.80},
            {"text": "90%", "start": 1.20, "end": 1.60},
        ],
        has_hook=True,
    )

    assert [cue.at for cue in plan.of_kind("pop")] == [1.20]


def test_hook_without_a_key_word_still_gets_a_pop_on_the_title_entrance():
    plan = build_sound_plan(
        keep_ranges=[(0.0, 30.0)],
        duration=30.0,
        words=[{"text": "bonjour", "start": 0.40, "end": 0.80}],
        has_hook=True,
    )

    assert [cue.at for cue in plan.of_kind("pop")] == [0.12]


def test_no_pop_when_the_clip_has_no_hook():
    plan = build_sound_plan(
        keep_ranges=[(0.0, 30.0)],
        duration=30.0,
        words=[{"text": "90%", "start": 1.20, "end": 1.60}],
        has_hook=False,
    )

    assert plan.of_kind("pop") == []


def test_rise_leads_the_punchline():
    plan = build_sound_plan(
        keep_ranges=[(0.0, 30.0)],
        duration=30.0,
        words=[
            {"text": "90%", "start": 1.20, "end": 1.60},
            {"text": "tu", "start": 7.40, "end": 7.60},
            {"text": "gagnes", "start": 7.60, "end": 8.00},
            {"text": "10x", "start": 8.00, "end": 8.50},
        ],
        has_hook=True,
    )

    # Dernier mot emphase apres la fenetre du hook : 8.00s, rise a -0.9s.
    assert [cue.at for cue in plan.of_kind("rise")] == [7.10]


def test_no_rise_when_the_only_emphasis_sits_inside_the_hook():
    plan = build_sound_plan(
        keep_ranges=[(0.0, 30.0)],
        duration=30.0,
        words=[{"text": "90%", "start": 1.20, "end": 1.60}],
        has_hook=True,
    )

    assert plan.of_kind("rise") == []
