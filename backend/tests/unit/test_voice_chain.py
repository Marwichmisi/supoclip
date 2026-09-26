"""T5 — Voix pro : contrat pur de la chaine (spec #12, ticket #20).

Ordre imperatif : coupe-bas, denoise, de-esser, EQ presence, compression,
limiteur, normalisation en dernier (chez l'appelant, pas ici).
"""

from src.media.voice import VOICE_STAGES, build_voice_filter


def test_the_voice_chain_follows_the_spec_order():
    stages = [fragment.split("=")[0] for fragment in build_voice_filter().split(",")]
    assert stages == list(VOICE_STAGES), stages


def test_the_chain_holds_the_six_moderate_stages_and_no_loudnorm():
    filt = build_voice_filter()
    assert "highpass=f=80" in filt
    assert "afftdn=nr=12:nf=-25" in filt
    assert "deesser=i=0.25" in filt
    assert "equalizer=f=3500" in filt
    assert "acompressor=threshold=0.125:ratio=2" in filt
    assert "makeup=2" in filt
    assert "alimiter=limit=0.95" in filt
    # La normalisation ferme la chaine chez l'appelant, jamais ici.
    assert "loudnorm" not in filt


def test_denoise_stays_moderate_never_aggressive():
    filt = build_voice_filter()
    # nr=12 : defaut ffmpeg, seuil d'artefacts au-dela de 15.
    assert "nr=12" in filt
    assert "nr=15" not in filt
