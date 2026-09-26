"""T4 — Sound design 100% local (spec #12, ticket #16).

Le rendu est incremente d'un lit musical CC0 embarque, duckee sous la voix par
sidechain (la voix n'est jamais duckee), et de SFX cales sur les timecodes du
clip : whoosh sur chaque cut, pop sur le hook, rise avant la punchline. Aucun
appel reseau au runtime, normalisation finale au standard plateforme.

Deux couches :

- ``build_sound_plan`` est pur : il ne connait que les timecodes du clip et
  decide *ou* les SFX tombent. Rien a I/O, donc testable sans ffmpeg.
- ``build_audio_mix_graph`` transforme le plan en fragment ``filter_complex``
  audio, a partir de fichiers locaux resolus par le manifest d'assets.
"""

from __future__ import annotations

import hashlib
import json
import math
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..assets_manifests import ManifestError, load_manifest, manifest_path_for
from ..clip_source_map import normalize_source_ranges
from ..emoji_captions import annotate_caption_words
from .common import (
    HOOK_TITLE_SECONDS,
    LOUDNESS_TARGET_LRA,
    LOUDNESS_TARGET_LUFS,
    LOUDNESS_TARGET_TP,
    logger,
)
from .ffmpeg import crossfade_fade_for_ranges, run_ffmpeg_command
from .timeline import get_words_for_keep_ranges
from .transcription import load_cached_transcript_data

#: Gain du lit musical avant ducking, en dB. Le lit se tient sous la voix — le
#: spec #12 demande un « lit pre-normalise bas » — mais assez haut pour
#: s'entendre : `loudnorm` normalise ensuite le mix d'ensemble, ce qui abaisse
#: aussi le lit. Mesure sur le bundle livre (voix de test a -16 dBFS sous la
#: parole), decalage lit / voix obtenu dans les creux :
#:
#:     -20 dB -> 30 a 34 dB sous la voix : inaudible
#:     -12 dB -> 17 a 20 dB    -10 dB -> 15 a 19 dB    -8 dB -> 13 a 17 dB
#:
#: -10 dB garde 5 dB de marge avant que le lit ne se couche sur la parole, tout
#: en restant largement sous elle. `test_sound_render.py` reverifie ce decalage.
BED_GAIN_DB = -10.0

#: Gain des SFX par rapport a la voix, en dB : ils doivent se voir sans masquer
#: la parole.
SFX_GAIN_DB = -10.0

#: Fondu d'entree/sortie anti-pop applique a chaque SFX, en secondes.
SFX_FADE_IN = 0.005
SFX_FADE_OUT = 0.020

#: Deux SFX ne doivent jamais se chevaucher : distance minimale entre cues.
MIN_CUE_GAP = 0.60

#: Fenetre du hook : celle du titre anime par les captions. Le pop tombe dedans,
#: et la punchline que le rise annonce est cherchee *apres* — alias, pas copie.
HOOK_WINDOW = HOOK_TITLE_SECONDS

#: Entree de la kinetic typography du hook : point de chute du pop quand le clip
#: n'a pas de mot-cle a emphaser (cf. T3).
HOOK_CUE_AT = 0.12

#: Le rise commence RISE_LEAD secondes avant la punchline et dure ~1.15s.
RISE_LEAD = 0.9

#: Roles portes par le premier mot-cle d'un asset audio du manifest.
BED_ROLE = "bed"
SFX_ROLE = "sfx"

#: Cues SFX que le plan sait poser. Ce sont aussi les seuls kinds acceptes par
#: `SoundLibrary.sfx_for` : un kind inconnu est une faute de programmation, pas
#: un asset manquant.
CUE_KINDS = ("whoosh", "pop", "rise")


@dataclass(frozen=True)
class SoundAsset:
    """Un fichier audio embarque, avec sa preuve de licence CC0."""

    id: str
    path: Path
    duration: float
    license: str
    source_url: str
    published_at: str
    sha256: str
    role: str
    cue_kind: Optional[str] = None

    def integrity_ok(self) -> bool:
        """Le fichier livre est-il bien celui que le manifest atteste ?"""
        try:
            return hashlib.sha256(self.path.read_bytes()).hexdigest() == self.sha256
        except OSError:
            return False


class SoundLibrary:
    """Ce que le bundle embarque permet de jouer, resolu une fois pour le rendu.

    Une bibliotheque vide est un etat valide : elle signifie simplement que
    l'exemplaire n'embarque pas (ou n'a plus) d'assets audio, et le rendu doit
    alors produire une voix nue plutot qu'echouer.
    """

    def __init__(self, assets: Sequence[SoundAsset] = ()) -> None:
        self._assets = tuple(assets)
        self._beds = tuple(a for a in self._assets if a.role == BED_ROLE)
        by_kind: Dict[str, List[SoundAsset]] = {}
        for asset in self._assets:
            if asset.role == SFX_ROLE and asset.cue_kind:
                by_kind.setdefault(asset.cue_kind, []).append(asset)
        self._sfx = {kind: tuple(items) for kind, items in by_kind.items()}

    @property
    def beds(self) -> Tuple[SoundAsset, ...]:
        return self._beds

    @property
    def is_empty(self) -> bool:
        return not self._beds and not self._sfx

    def sfx_for(self, kind: str) -> Tuple[SoundAsset, ...]:
        """Variantes d'un SFX, du plus ancien au plus recent dans le manifest."""
        if kind not in CUE_KINDS:
            raise ValueError(
                f"unknown sound cue kind: {kind!r} (expected one of {list(CUE_KINDS)})"
            )
        return self._sfx.get(kind, ())

    def all_assets(self) -> Tuple[SoundAsset, ...]:
        return self._assets

    def pick_bed(self, seed: str) -> Optional[SoundAsset]:
        """Lit musical du clip. Le meme seed donne toujours le meme lit.

        Le choix est derive d'un CRC du seed (et non du ``hash()`` de Python,
        sale par process) pour que deux rendus d'un meme clip — un worker, un
        export — entendent la meme musique.
        """
        if not self._beds:
            return None
        return self._beds[zlib.crc32(seed.encode("utf-8")) % len(self._beds)]

    def pick_sfx(self, kind: str, index: int) -> Optional[SoundAsset]:
        """Variante du SFX pour le n-ieme cue de ce kind, en boucle."""
        variants = self.sfx_for(kind)
        if not variants:
            return None
        return variants[index % len(variants)]


def _classify(keywords: Sequence[str]) -> Tuple[str, Optional[str]]:
    """(role, kind) d'un asset d'apres ses mots-cles : 'bed' ou 'sfx' <kind>."""
    tokens = [str(token).lower() for token in keywords]
    if BED_ROLE in tokens:
        return BED_ROLE, None
    if SFX_ROLE in tokens:
        index = tokens.index(SFX_ROLE)
        kind = tokens[index + 1] if index + 1 < len(tokens) else None
        return SFX_ROLE, kind
    return "", None


def load_sound_library(manifest_path: Optional[Path] = None) -> SoundLibrary:
    """Charge les assets audio embarques depuis leur manifest versionne.

    Un manifeste illisible ou un fichier absent ecarte l'asset concerne avec un
    warning : le rendu ne doit jamais dies sur un bundle incomplet. Un asset dont
    le sha256 ne correspond plus est ecarte aussi — le spec #12 conditionne la
    presence du bundle dans le repo public a la validite de sa preuve de licence.
    """
    path = Path(manifest_path) if manifest_path else manifest_path_for("audio")
    try:
        manifest = load_manifest(path)
    except ManifestError as exc:
        logger.warning("Sound design off: audio manifest unusable (%s)", exc)
        return SoundLibrary()

    # Les chemins du manifest sont relatifs a sa racine versionnee
    # (<racine>/audio/manifest.json -> <racine>), jamais a un repertoire
    # devine : un bundle de test et le bundle livre se resolvent de meme.
    root = path.resolve().parent.parent
    assets: List[SoundAsset] = []
    for entry in manifest["assets"]:
        role, cue_kind = _classify(entry.get("keywords") or [])
        if not role:
            logger.debug("Sound design: asset %s sans role, ignore", entry["id"])
            continue
        asset_path = root / entry["path"]
        if not asset_path.is_file():
            logger.warning(
                "Sound design: asset %s absent (%s), ignore", entry["id"], asset_path
            )
            continue
        asset = SoundAsset(
            id=entry["id"],
            path=asset_path,
            duration=float(entry["duration"]),
            license=entry["license"],
            source_url=entry["source_url"],
            published_at=entry["published_at"],
            sha256=entry["sha256"],
            role=role,
            cue_kind=cue_kind,
        )
        if not asset.integrity_ok():
            logger.warning(
                "Sound design: sha256 de %s different du manifest, ignore", entry["id"]
            )
            continue
        assets.append(asset)
    return SoundLibrary(assets)


@dataclass(frozen=True)
class SfxCue:
    """Un SFX a poser sur la timeline de sortie du clip."""

    kind: str
    at: float
    gain_db: float = SFX_GAIN_DB


@dataclass(frozen=True)
class SoundPlan:
    """Timecodes des SFX d'un clip, sans reference aux fichiers."""

    duration: float
    cues: List[SfxCue] = field(default_factory=list)

    def of_kind(self, kind: str) -> List[SfxCue]:
        return [cue for cue in self.cues if cue.kind == kind]


def _cut_times(keep_ranges: Sequence[Tuple[float, float]]) -> List[float]:
    """Positions des cuts internes sur la timeline de sortie, en secondes.

    Le meme report que ``get_words_for_keep_ranges`` : chaque jonction racle le
    crossfade, donc le cut i arrive ``fade * i`` plus tot que la somme brute des
    ranges. Un whoosh pose sur la version non comptee serait decale d'un cheveu
    du cut qu'il annonce.
    """
    ranges = normalize_source_ranges(list(keep_ranges))
    fade = crossfade_fade_for_ranges(ranges)
    times: List[float] = []
    offset = ranges[0][1] - ranges[0][0]
    for index in range(1, len(ranges)):
        times.append(round(offset - fade, 6))
        offset += (ranges[index][1] - ranges[index][0]) - fade
    return times


def build_sound_plan(
    *,
    keep_ranges: Sequence[Tuple[float, float]],
    duration: float,
    words: Sequence[Mapping[str, Any]] = (),
    has_hook: bool = False,
) -> SoundPlan:
    """Timecodes des SFX du clip : whoosh sur chaque cut, pop au hook, rise
    avant la punchline.

    ``words`` sont les mots du clip projetes sur la timeline de sortie (meme
    source que les captions). Ils ne servent qu'a ancrer le pop et le rise ;
    sans eux, seuls les whoosh sont poses.
    """
    cues = [
        SfxCue(kind="whoosh", at=at)
        for at in _cut_times(keep_ranges)
        if at > 0 and at < duration
    ]

    if has_hook:
        cues.append(SfxCue(kind="pop", at=_hook_anchor(words)))
    punchline = _punchline_start(words)
    if punchline is not None:
        cues.append(
            SfxCue(kind="rise", at=max(0.0, round(punchline - RISE_LEAD, 6)))
        )

    return SoundPlan(duration=duration, cues=_spaced(cues, duration))


def _emphasis_starts(words: Sequence[Mapping[str, Any]]) -> List[float]:
    """Debuts (en secondes) des mots emphases, dans l'ordre du clip.

    On reutilise l'annotation des captions : un mot est emphase quand c'est un
    mot-cle de pouvoir ou quand il porte un chiffre. Les emojis sont desactives
    (ils dependent du rendu de police) pour que le plan soit deterministe.
    """
    if not words:
        return []
    _, emphasis = annotate_caption_words(
        list(words), None, enable_emoji=False, enable_emphasis=True
    )
    return [
        float(words[index].get("start", 0.0))
        for index in sorted(emphasis)
        if index < len(words)
    ]


def _hook_anchor(words: Sequence[Mapping[str, Any]]) -> float:
    """Le pop tombe sur le premier mot-cle du hook, sinon sur l'entree du titre.

    Sans mot-cle, on vise l'entree de la kinetic typography (0.12s, cf. T3) :
    le pop accompagne alors l'apparition du titre.
    """
    inside = [start for start in _emphasis_starts(words) if start < HOOK_WINDOW]
    if inside:
        return round(inside[0], 6)
    return HOOK_CUE_AT


def _punchline_start(words: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Debut du dernier mot emphase hors fenetre du hook, s'il existe.

    Le hook a deja son pop : on cherche le moment fort *suivant*. C'est la
    punchline que le rise annonce.
    """
    after = [start for start in _emphasis_starts(words) if start >= HOOK_WINDOW]
    if not after:
        return None
    return max(after)


def _spaced(cues: List[SfxCue], duration: float) -> List[SfxCue]:
    """Trie les cues et ecarte ceux qui se chevaucheraient, du plus tot au plus tard."""
    kept: List[SfxCue] = []
    for cue in sorted(cues, key=lambda item: item.at):
        if not 0 <= cue.at <= duration:
            continue
        if kept and cue.at - kept[-1].at < MIN_CUE_GAP:
            continue
        kept.append(cue)
    return kept


# --- Mixage -----------------------------------------------------------------

#: Niveau final vise : -14 LUFS integres, plafond true peak a -1.5 dBTP, plage
#: LRA resserree a 11 LU — la cible des plateformes courtes, et la meme que le
#: `LOUDNORM_FILTER` du chemin sans sound design (constantes importees de
#: `common`, un seul endroit ou les regler).

#: Etiquette de sortie du graphe audio, celle que la passe finale mappe.
MIX_OUTPUT_LABEL = "aout"

#: Attaque et release du sidechain. Attaque courte (20 ms, le defaut ffmpeg) :
#: le lit s'ecarte des que la parole commence. Release de 300 ms et non 500 :
#: sur une cadence de parole serree (1.2s de parole / 0.8s de silence, soit le
#: rythme d'un clip court), 500 ms ne laisse jamais le lit remonter entre deux
#: phrases — il restait 8 dB ducke en plein silence. Decalage lit / voice mesure
#: dans les creux sur le bundle livre :
#:
#:     500 ms -> 30 a 34 dB sous la voix (le lit ne remonte jamais)
#:     300 ms -> 15 a 19 dB        200 ms -> 22 a 26 dB (remontée trop nerveuse)
#:
#: 300 ms laisse le lit respirer entre les phrases sans le faire palpiter.
DUCK_ATTACK_MS = 20
DUCK_RELEASE_MS = 300

#: Seuil et ratio du sidechain : le seuil dit a partir de quand le lit s'ecarte,
#: le ratio de combien. ffmpeg mesure la cle en RMS (son mode par defaut), donc
#: la profondeur depend des deux — d'ou les deux reglages plutot qu'un seul.
#: Mesures sur le bundle livre (lit seul, voix synthetique 6 kHz gatee,
#: profondeur = niveau du lit dans les creux moins niveau sous la parole) :
#:
#:     ratio 1.0 ->  1.0 dB    ratio 2.0 ->  8.1 dB    ratio 2.5 ->  9.6 dB
#:     ratio 3.0 -> 10.6 dB    ratio 4.0 -> 11.8 dB
#:
#: 2.5 donne ~9.6 dB pour les 6 demandes par la recherche #4, en gardant le lit
#: sous la voix sans l'effacer completement. `test_sound_render.py` reverifie
#: la profondeur a chaque build.
DUCK_THRESHOLD = 0.02
DUCK_RATIO = 2.5


@dataclass(frozen=True)
class MixGraph:
    """Le graphe audio de la passe finale, pret a etre concatene au graphe video.

    ``input_args`` sont les ``-i`` supplementaires a passer a ffmpeg apres
    l'entree du clip, ``graph`` le fragment ``filter_complex`` audio et
    ``map_args`` le mappage de sortie. Les trois vont ensemble : separer le
    graphe de ses entrees produirait un filtre referencant des index
    inexistants.
    """

    input_args: List[str]
    graph: str
    map_args: List[str]


def build_loudness_filter(
    measured: Optional[Mapping[str, Any]] = None,
    print_format: Optional[str] = None,
) -> str:
    """Filtre `loudnorm` du render, eventuellement en deux passes.

    En deux passes, la premiere mesure (print_format=json) et la seconde
    recoit les valeurs mesurees : le resultat tient reellement la cible, ce que
    le mode dynamique ne garantit pas. Sans mesure, on retombe sur le mode
    dynamique d'origine, qui est moins juste mais toujours meilleur que rien.
    """
    parts = [
        f"I={LOUDNESS_TARGET_LUFS}",
        f"TP={LOUDNESS_TARGET_TP}",
        f"LRA={LOUDNESS_TARGET_LRA}",
    ]
    if measured:
        parts += [f"{key}={value}" for key, value in measured.items()]
    if print_format:
        parts.append(f"print_format={print_format}")
    return "loudnorm=" + ":".join(parts)


def build_audio_mix_graph(
    *,
    plan: SoundPlan,
    library: SoundLibrary,
    seed: str,
    measured: Optional[Mapping[str, Any]] = None,
    print_format: Optional[str] = None,
) -> Optional[MixGraph]:
    """Chaine audio de la passe finale : voix pro + lit ducke + SFX -> loudnorm.

    T5 voix pro : la voix passe d'abord par la chaine voix (coupe-bas,
    denoise modere, de-esser, EQ, compression, limiteur), avant le split
    vers le mix et vers la cle sidechain. La cle voit donc la meme voix
    nettoyee, ce qui garde le ducking calibre en T4 (ton 6 kHz a +0.1 dB).
    Le lit et les SFX ne sont jamais filtres par la chaine voix.

    Renvoie None si le bundle n'a rien a jouer, pour que l'appelant garde le
    chemin voix nue. L'entree 0 est le clip lui-meme ; le lit et les SFX
    occupent les entrees suivantes, dans l'ordre du graphe.
    """
    bed = library.pick_bed(seed)
    placements = _place_cues(plan, library)
    if not bed and not placements:
        return None

    from .voice import build_voice_filter

    voice_filter = build_voice_filter()

    input_args: List[str] = []
    parts: List[str] = []
    next_input = 1

    bed_index: Optional[int] = None
    if bed:
        # Le lit est plus court que le clip : on le fait boucler plutot que de
        # laisser un trou de silence sous la voix.
        input_args += ["-stream_loop", "-1", "-i", str(bed.path)]
        bed_index = next_input
        next_input += 1
        parts.append(
            f"[{bed_index}:a]volume={BED_GAIN_DB}dB,atrim=0:{plan.duration:.3f}[bedraw]"
        )
        parts.append(f"[0:a]{voice_filter},asplit=2[voice][voicekey]")
        parts.append(
            f"[bedraw][voicekey]sidechaincompress="
            f"threshold={DUCK_THRESHOLD}:ratio={DUCK_RATIO}"
            f":attack={DUCK_ATTACK_MS}:release={DUCK_RELEASE_MS}"
            f":makeup=1[bed]"
        )
    else:
        parts.append(f"[0:a]{voice_filter},anull[voice]")

    sfx_labels: List[str] = []
    for index, (cue, asset) in enumerate(placements):
        input_index = next_input
        next_input += 1
        input_args += ["-i", str(asset.path)]
        delay_ms = int(round(cue.at * 1000))
        fade_out_at = max(0.0, asset.duration - SFX_FADE_OUT)
        label = f"sfx{index}"
        # Les fondus passent AVANT le decalage, et c'est obligatoire : `afade`
        # raisonne sur l'horloge de la piste qu'il recoit. Pose apres `adelay`,
        # le fondu de sortie se terminerait dans les 2 secondes de silence
        # introduites et laisserait le SFX entier a zero — on n'entendrait rien.
        # Ici l'horloge commence a 0, celle du SFX, et `adelay` ne fait que
        # translator la piste deja fondue sur le timecode du cue.
        parts.append(
            f"[{input_index}:a]volume={cue.gain_db}dB,"
            f"afade=t=in:st=0:d={SFX_FADE_IN},"
            f"afade=t=out:st={fade_out_at:.3f}:d={SFX_FADE_OUT},"
            f"adelay={delay_ms}:all=1[{label}]"
        )
        sfx_labels.append(label)

    mix_inputs = ["[voice]"] + (["[bed]"] if bed_index is not None else []) + [
        f"[{label}]" for label in sfx_labels
    ]
    parts.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:normalize=0[mixed]"
    )
    parts.append(
        f"[mixed]{build_loudness_filter(measured, print_format)}[{MIX_OUTPUT_LABEL}]"
    )

    return MixGraph(
        input_args=input_args, graph=";".join(parts), map_args=["-map", f"[{MIX_OUTPUT_LABEL}]"]
    )


def _place_cues(
    plan: SoundPlan, library: SoundLibrary
) -> List[Tuple[SfxCue, SoundAsset]]:
    """Associe chaque cue a un fichier du bundle, en alternant les variantes.

    Deux cuts qui se suivent ne doivent pas entendre le meme whoosh : on
    tourne dans les variantes disponibles plutot que de repeter le premier
    fichier trouve.
    """
    placed: List[Tuple[SfxCue, SoundAsset]] = []
    counters: Dict[str, int] = {}
    for cue in sorted(plan.cues, key=lambda item: item.at):
        asset = library.pick_sfx(cue.kind, counters.get(cue.kind, 0))
        if asset is None:
            logger.info("Sound design: no local '%s' SFX, cue skipped", cue.kind)
            continue
        counters[cue.kind] = counters.get(cue.kind, 0) + 1
        placed.append((cue, asset))
    return placed


# --- Chaine audio d'un clip -------------------------------------------------


@dataclass(frozen=True)
class ClipSound:
    """La chaine audio d'un clip : son plan de cues et le bundle qui le realise.

    C'est ce que l'appelant passe a la passe finale ; le graphe n'est pas
    construit ici parce que la normalisation en deux passes a besoin de mesurer
    le mix avant de fixer le loudnorm final.
    """

    plan: SoundPlan
    library: SoundLibrary
    seed: str


def prepare_clip_sound(
    video_path: Path,
    keep_ranges: Sequence[Tuple[float, float]],
    *,
    duration: float,
    has_hook: bool = False,
    seed: str = "",
    library: Optional[SoundLibrary] = None,
) -> Optional[ClipSound]:
    """Chaine audio d'un clip, ou None si rien n'a a etre joue.

    Les mots viennent du cache de transcription, projetes sur la timeline de
    sortie exactement comme les captions : c'est ce qui garantit qu'un pop ou
    un rise tombe sur le mot qu'il annonce. Sans transcription, seuls les
    whoosh (qui dependent des seuls cuts) sont poses.
    """
    resolved = library if library is not None else load_sound_library()
    if resolved.is_empty:
        return None

    words: List[Mapping[str, Any]] = []
    transcript = load_cached_transcript_data(video_path)
    if transcript and transcript.get("words"):
        words = get_words_for_keep_ranges(transcript, list(keep_ranges))

    plan = build_sound_plan(
        keep_ranges=keep_ranges,
        duration=duration,
        words=words,
        has_hook=has_hook,
    )
    if not plan.cues and not resolved.beds:
        return None
    return ClipSound(plan=plan, library=resolved, seed=seed or video_path.name)


def bed_seed(video_path: Path, keep_ranges: Sequence[Tuple[float, float]]) -> str:
    """Identite du lit musical d'un clip, stable a travers ses re-rendus.

    On separe le nom du fichier de sortie, qui contient un uuid a chaque passe :
    l'utiliser comme graine ferait changer de musique au re-render d'une
    variante de hook ou de captions, et la comparaison entre variantes — le
    coeur de la story 7 de la spec — comparerait alors deux musiques. Ce qui
    definit un clip, c'est sa source et ses ranges conservees : les deux sont
    stables d'un rendu a l'autre.
    """
    ranges = ",".join(f"{start:.2f}-{end:.2f}" for start, end in keep_ranges)
    return f"{video_path.stem}|{ranges}"


def mix_graph_for(
    sound: ClipSound, measured: Optional[Mapping[str, Any]] = None
) -> Optional[MixGraph]:
    """Graphe de mixage d'un clip, mesure fournie ou non (voir `measure_loudness`).

    None si le bundle n'a rien a jouer : l'appelant garde alors le chemin voix
    nue, et le rendu ne perd rien de ce qui existait avant le sound design.
    """
    return build_audio_mix_graph(
        plan=sound.plan, library=sound.library, seed=sound.seed, measured=measured
    )


#: Cles de sortie de `loudnorm` renommees en options de filtre pour la 2e passe.
LOUDNESS_MEASURED_KEYS = (
    ("input_i", "measured_I"),
    ("input_tp", "measured_TP"),
    ("input_lra", "measured_LRA"),
    ("input_thresh", "measured_thresh"),
    ("target_offset", "offset"),
)


def measure_loudness(
    input_path: Path,
    sound: ClipSound,
    timeout: int = 600,
) -> Optional[Dict[str, Any]]:
    """Mesure le mix en deux passes : valeurs `loudnorm` de la premiere passe.

    La passe de mesure ne decode que l'audio (aucune sortie video), et ne
    produit aucun fichier : c'est une analyse, pas un rendu. Renvoie None si la
    mesure echoue ou n'est pas exploitable — l'appelant retombera alors sur le
    mode dynamique, moins juste mais toujours bien mieux qu'un clip sans
    normalisation.
    """
    probe = build_audio_mix_graph(
        plan=sound.plan,
        library=sound.library,
        seed=sound.seed,
        print_format="json",
    )
    if probe is None:
        return None

    command = [
        "ffmpeg", "-y", "-i", str(input_path), *probe.input_args,
        "-filter_complex", probe.graph,
        *probe.map_args, "-vn", "-f", "null", "-",
    ]
    result = run_ffmpeg_command(command, timeout=timeout)
    if result.returncode != 0:
        logger.warning("Loudness measurement failed; using dynamic loudnorm")
        return None

    values = _parse_loudnorm_json(result.stderr or result.stdout)
    if not values:
        logger.warning("No loudnorm measurement found; using dynamic loudnorm")
        return None
    return values


def _parse_loudnorm_json(output: str) -> Optional[Dict[str, Any]]:
    """Extrait le bloc JSON de `loudnorm ... print_format=json` de la sortie ffmpeg.

    Les valeurs non finies sont ecartees. C'est le cas d'un clip dont le mix
    n'est quasiment pas audible (source muette, ou SFX seuls sur un plan sans
    lit) : `loudnorm` y renvoie "-inf", et le lui repasser en deuxieme passe
    fait echouer le rendu. On rend donc la main au mode dynamique, qui sait
    encore normaliser ce genre de signal.
    """
    start = output.rfind("{")
    end = output.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(output[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    # Les cles de sortie de loudnorm ne sont pas celles du filtre : on les
    # renomme pour que la seconde passe sache quoi en faire.
    values = {}
    for key, option in LOUDNESS_MEASURED_KEYS:
        if key not in payload:
            continue
        try:
            number = float(payload[key])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(number):
            continue
        values[option] = payload[key]
    if "measured_I" not in values:
        return None
    return values
