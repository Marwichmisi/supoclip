"""T5 — Voix pro 100% locale (spec #12, ticket #20).

Chaine voix dans l'ordre imperatif du spec :

    coupe-bas, denoise modere, de-esser, EQ presence,
    compression, limiteur, normalisation en dernier.

La normalisation (loudnorm) n'est pas dans ce module : elle ferme le mix
dans `sound.build_audio_mix_graph` (deux passes avec mesure) ou dans
`ffmpeg.build_audio_output_args` (voix nue). Ce module ne fournit que les
six premiers maillons, purs et testables sans ffmpeg.

Reglages moderes, calibres pour :
- reduire le souffle phone/street sans artefacts musicaux (afftdn nr=12,
  pas au-dela) ;
- preserver la voix T4 (ton 6 kHz de test a +0.1 dB) afin que le ducking
  sidechain (seuil/ratio calibres sur voix nue) et les seuils T4 restent
  valides ;
- ne jamais toucher la chaine transcription (cf. `transcription.py`, qui
  extrait l'audio brut sans passer par ce filtre).
"""

from __future__ import annotations

#: Coupe-bas : 80 Hz, 2 poles. Enleve rumble/vent/plosives sans toucher
#: la voix (fondamental > 100 Hz).
HIGHPASS_FREQ = 80

#: Denoise modere : nr=12 (defaut ffmpeg, jamais agressif), nf=-25.
#: nf=-50 (defaut) ne retire rien sur souffle phone ; nf=-25 retire
#: ~5 dB de hiss avec makeup=2, sans artefacts musicaux. Au-dela (nr>15),
#: le risque d'artefacts l'emporte sur le gain.
DENOISE_NR = 12.0
DENOISE_NF = -25.0

#: De-esser modere : i=0.25 attenue la sibilance 8 kHz de ~3 dB tout en
#: preservant le ton 6 kHz T4 a -1 dB. i>=0.3 tue le 6 kHz (-5 dB et plus),
#: i=0.2 ne fait presque rien (-0.5 dB sur 8 kHz).
DEESSER_INTENSITY = 0.25
DEESSER_MAX = 0.5
DEESSER_FREQ = 0.6

#: EQ presence : +3 dB a 3.5 kHz, 1 octave. Intelligibilite phone/street.
EQ_FREQ = 3500.0
EQ_WIDTH = 1.0
EQ_GAIN_DB = 3.0

#: Compression douce 2:1, seuil -18 dB (0.125), attack 20 ms / release 200 ms,
#: makeup x2 (+6 dB) pour compenser l'attenuation et preserver la balance
#: voix/lit calibree en T4 (ton 6 kHz a -0.4 dB avec makeup=2, -8.6 dB sans).
COMP_THRESHOLD = 0.125
COMP_RATIO = 2.0
COMP_ATTACK = 20.0
COMP_RELEASE = 200.0
COMP_MAKEUP = 2.0

#: Limiteur plafond -0.45 dB (0.95) : anti-clip avant loudnorm, qui tient
#: ensuite le vrai plafond true peak a -1.5 dBTP.
LIMITER_LIMIT = 0.95


def build_voice_filter() -> str:
    """Filtre audio voix pro, sans la normalisation finale.

    Ordre imperatif du spec #12 : coupe-bas, denoise, de-esser, EQ presence,
    compression, limiteur. La normalisation (loudnorm) ferme la chaine chez
    l'appelant, en dernier dans tous les chemins.
    """
    return (
        f"highpass=f={HIGHPASS_FREQ},"
        f"afftdn=nr={DENOISE_NR:g}:nf={DENOISE_NF:g},"
        f"deesser=i={DEESSER_INTENSITY:g}:m={DEESSER_MAX:g}:f={DEESSER_FREQ:g},"
        f"equalizer=f={EQ_FREQ:g}:t=o:w={EQ_WIDTH:g}:g={EQ_GAIN_DB:g},"
        f"acompressor=threshold={COMP_THRESHOLD:g}:ratio={COMP_RATIO:g}"
        f":attack={COMP_ATTACK:g}:release={COMP_RELEASE:g}:makeup={COMP_MAKEUP:g},"
        f"alimiter=limit={LIMITER_LIMIT:g}"
    )


#: Etapes attendues dans l'ordre, pour le garde d'ordre des tests.
VOICE_STAGES = (
    "highpass",
    "afftdn",
    "deesser",
    "equalizer",
    "acompressor",
    "alimiter",
)
