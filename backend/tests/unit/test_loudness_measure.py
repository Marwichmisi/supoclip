"""T4 — normalisation finale : la mesure loudnorm ne doit jamais casser le rendu.

`loudnorm` en deux passes est plus juste que son mode dynamique, mais il se
retourne contre nous sur un mix inaudible : la premiere passe renvoie alors
"-inf", et repasser "-inf" comme option de filtre fait echouer le rendu. La
mesure doit donc etre ecartee, pas transmise.
"""

import math

from src.media.sound import _parse_loudnorm_json


def _block(**values) -> str:
    """Le bloc JSON que ffmpeg affiche apres un filtre `print_format=json`."""
    entries = ",\n".join(f'    "{key}": "{value}"' for key, value in values.items())
    return f"[Parsed_loudnorm] \n{{\n{entries}\n}}\n[Parsed_loudnorm] \n"


def test_a_measurable_mix_is_forwarded_to_the_second_pass():
    values = _parse_loudnorm_json(
        _block(
            input_i="-18.42",
            input_tp="-3.10",
            input_lra="6.20",
            input_thresh="-28.90",
            target_offset="0.35",
            normalization_type="linear",
        )
    )

    assert values == {
        "measured_I": "-18.42",
        "measured_TP": "-3.10",
        "measured_LRA": "6.20",
        "measured_thresh": "-28.90",
        "offset": "0.35",
    }


def test_an_inaudible_mix_is_not_measured_at_all():
    """Un mix qui ne porte que des SFX sur une source muette mesure -inf.

    Le transmettre ferait echouer la deuxieme passe (ffmpeg sort en erreur et
    n'ecrit rien). On abandonne la mesure : le mode dynamique, lui, sait
    normaliser ce signal.
    """
    assert _parse_loudnorm_json(
        _block(
            input_i="-inf",
            input_tp="-inf",
            input_lra="0.00",
            input_thresh="-70.00",
            target_offset="inf",
        )
    ) is None


def test_non_finite_values_are_dropped_from_an_otherwise_valid_measurement():
    values = _parse_loudnorm_json(
        _block(input_i="-18.42", input_tp="-inf", input_lra="6.20", target_offset="inf")
    )

    assert values == {"measured_I": "-18.42", "measured_LRA": "6.20"}
    assert all(math.isfinite(float(v)) for v in values.values())


def test_a_measurement_without_integrated_loudness_is_refused():
    """`measured_I` pilote tout le calcul : sans lui, la passe ne tient pas."""
    assert _parse_loudnorm_json(_block(input_tp="-3.10", input_lra="6.20")) is None


def test_output_without_a_json_block_is_refused():
    assert _parse_loudnorm_json("ffmpeg version 6.0\nnothing to see here") is None
    assert _parse_loudnorm_json("") is None
