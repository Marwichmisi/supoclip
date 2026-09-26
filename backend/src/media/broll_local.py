"""T6 — B-roll local + blur fallback (spec #12, ticket #17).

Resolveur 100% local sur manifest versionne : un mot-cle vers un clip CC0
normalise 1080x1920. Quand ca matche, le B-roll est incrustre ; sinon le
blur cinematique existant reste (jamais de trou visuel).

Contraintes spec :
- chaque asset garde sa preuve de licence CC0 (URL + date + hash) ;
  un asset sans preuve est refuse au chargement (via ``assets_manifests``) ;
- zero appel reseau pendant le rendu : ce module n'importe ni httpx ni
  le resolveur Pexels historique (``src/broll.py`` reste dev-only) ;
- le blur fallback est le compositor vertical existant : ce module ne le
  reimplemente pas, il se contente de ne rien incruster quand il n'y a
  pas de match.

Deux couches, comme le sound design T4 :

- plan pur (``build_broll_plan``) : mots-cles -> cues sur la timeline
  de sortie, sans I/O donc testable sans ffmpeg ;
- ``apply_local_broll`` : le plan + des fichiers locaux -> overlay
  1080x1920 via le helper ``insert_broll_into_clip`` existant.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..assets_manifests import ManifestError, load_manifest, manifest_path_for
from .common import logger


#: Duree d'overlay par defaut quand l'opportunite n'en precise pas.
DEFAULT_BROLL_SECONDS = 3.0
#: Duree min/max d'un overlay, en secondes (meme bornes que le schema Pexels
#: historique pour ne pas changer le contrat de duree).
MIN_BROLL_SECONDS = 2.0
MAX_BROLL_SECONDS = 5.0
#: Nombre max d'overlays par clip : au-dela on hache le rythme.
MAX_OVERLAYS_PER_CLIP = 2


@dataclass(frozen=True)
class BrollAsset:
    """Un clip CC0 embarque, avec sa preuve de licence."""

    id: str
    path: Path
    duration: float
    license: str
    source_url: str
    published_at: str
    sha256: str
    keywords: Tuple[str, ...] = ()

    def integrity_ok(self) -> bool:
        """Le fichier livre est-il bien celui que le manifest atteste ?"""
        try:
            return hashlib.sha256(self.path.read_bytes()).hexdigest() == self.sha256
        except OSError:
            return False


@dataclass(frozen=True)
class BrollCue:
    """Un overlay B-roll pose sur la timeline de sortie du clip."""

    asset_id: str
    keyword: str
    at: float
    duration: float


@dataclass(frozen=True)
class BrollPlan:
    """Cues B-roll d'un clip, sans reference aux fichiers."""

    duration: float
    cues: List[BrollCue] = field(default_factory=list)


class BrollLibrary:
    """Ce que le bundle embarque permet d'incruster, resolu une fois.

    Une bibliotheque vide est un etat valide : elle signifie que
    l'exemplaire n'embarque pas (ou plus) de B-roll, et le rendu doit
    alors garder le blur cinematique plutot qu'echouer.
    """

    def __init__(self, assets: Sequence[BrollAsset] = ()) -> None:
        self._assets = tuple(assets)

    @property
    def is_empty(self) -> bool:
        return not self._assets

    def all_assets(self) -> Tuple[BrollAsset, ...]:
        return self._assets

    def asset_by_id(self, asset_id: str) -> Optional[BrollAsset]:
        for asset in self._assets:
            if asset.id == asset_id:
                return asset
        return None

    def resolve(self, keyword: str) -> Optional[BrollAsset]:
        """Meilleur asset local pour un mot-cle, ou None sans match."""
        return resolve_broll_for_keyword(keyword, self._assets)


def normalize_broll_keyword(value: Any) -> str:
    """Normalise un mot-cle pour la comparaison (insensible casse/accents)."""
    text = str(value or "")
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = stripped.casefold()
    collapsed = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    return re.sub(r"\s+", " ", collapsed)


def resolve_broll_for_keyword(
    keyword: str, assets: Sequence[BrollAsset]
) -> Optional[BrollAsset]:
    """Resolveur local : mot-cle -> clip CC0, ou None.

    Ordre deterministe (manifest d'abord) :
    1. egalite normalisee exacte sur un mot-cle d'asset ;
    2. inclusion normalisee (mot-cle dans keyword d'asset ou inverse,
       min 3 caracteres pour eviter les faux positifs).
    """
    wanted = normalize_broll_keyword(keyword)
    if not wanted or len(wanted) < 2:
        return None
    normalized_assets: List[Tuple[BrollAsset, List[str]]] = []
    for asset in assets:
        tokens = [normalize_broll_keyword(k) for k in asset.keywords]
        normalized_assets.append((asset, [t for t in tokens if t]))

    for asset, tokens in normalized_assets:
        if wanted in tokens:
            return asset
    for asset, tokens in normalized_assets:
        for token in tokens:
            if len(token) < 3 or len(wanted) < 3:
                continue
            if wanted in token or token in wanted:
                return asset
    return None


def _clamp_broll_seconds(value: Any) -> float:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_BROLL_SECONDS
    if seconds <= 0:
        return DEFAULT_BROLL_SECONDS
    return min(MAX_BROLL_SECONDS, max(MIN_BROLL_SECONDS, seconds))


def _opportunity_keyword(opportunity: Mapping[str, Any]) -> str:
    for key in ("keyword", "search_term", "broll", "visual", "query", "mot_cle"):
        raw = opportunity.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _opportunity_at(opportunity: Mapping[str, Any], fallback: float) -> float:
    raw = opportunity.get("timestamp", opportunity.get("at", fallback))
    if isinstance(raw, str) and ":" in raw:
        try:
            minutes, seconds = raw.split(":", 1)
            return max(0.0, float(minutes) * 60.0 + float(seconds))
        except ValueError:
            return max(0.0, fallback)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return max(0.0, fallback)


def build_broll_plan(
    opportunities: Sequence[Mapping[str, Any]],
    library: BrollLibrary,
    *,
    clip_duration: float,
    max_overlays: int = MAX_OVERLAYS_PER_CLIP,
) -> BrollPlan:
    """Construit les cues B-roll d'un clip depuis des opportunites texte.

    Chaque opportunite porte un mot-cle (``keyword`` / ``search_term`` FR/EN)
    plus un placement (``timestamp``/``at`` en secondes ou ``MM:SS``) et une
    duree optionnelle. Seuls les mots-cles resolus localement deviennent des
    cues ; le reste est ignore (blur fallback, jamais de trou visuel).
    Les cues sont dedupliques par asset et bornes a ``max_overlays``.
    """
    duration = max(0.0, float(clip_duration or 0.0))
    cues: List[BrollCue] = []
    seen_assets: set[str] = set()
    for index, opportunity in enumerate(opportunities or ()):
        if len(cues) >= max(1, max_overlays):
            break
        if not isinstance(opportunity, Mapping):
            continue
        keyword = _opportunity_keyword(opportunity)
        if not keyword:
            continue
        asset = library.resolve(keyword)
        if asset is None:
            continue
        if asset.id in seen_assets:
            continue
        at = _opportunity_at(opportunity, fallback=float(index))
        if at >= duration or duration <= 0:
            continue
        cue_duration = _clamp_broll_seconds(
            opportunity.get("duration", DEFAULT_BROLL_SECONDS)
        )
        cue_duration = min(cue_duration, max(0.0, duration - at))
        if cue_duration < 1.0:
            continue
        seen_assets.add(asset.id)
        cues.append(
            BrollCue(
                asset_id=asset.id,
                keyword=keyword,
                at=round(at, 3),
                duration=round(cue_duration, 3),
            )
        )
    cues.sort(key=lambda cue: cue.at)
    return BrollPlan(duration=duration, cues=cues)


def plan_broll_for_transcript(
    transcript: str,
    library: BrollLibrary,
    *,
    clip_duration: float,
    max_overlays: int = MAX_OVERLAYS_PER_CLIP,
) -> BrollPlan:
    """Plan B-roll depuis un texte libre (fallback sans analyse IA).

    Un mot-cle d'asset present (normalise) dans le transcript devient une
    opportunite ; les overlays sont repartis sur la duree du clip pour ne
    jamais se chevaucher. Sans match, le plan est vide (blur fallback).
    """
    text = normalize_broll_keyword(transcript)
    if not text or library.is_empty or clip_duration <= 0:
        return BrollPlan(duration=max(0.0, float(clip_duration or 0.0)), cues=[])
    haystack = f" {text} "
    opportunities: List[Dict[str, Any]] = []
    slot = max(1.0, float(clip_duration) / max(1, max_overlays + 1))
    for asset in library.all_assets():
        for keyword in asset.keywords:
            token = normalize_broll_keyword(keyword)
            if len(token) < 3 or f" {token} " not in haystack:
                continue
            at = min(
                max(0.0, float(clip_duration) - MIN_BROLL_SECONDS),
                slot * (len(opportunities) + 1),
            )
            opportunities.append(
                {"keyword": keyword, "timestamp": at, "duration": DEFAULT_BROLL_SECONDS}
            )
            break
        if len(opportunities) >= max(1, max_overlays):
            break
    return build_broll_plan(
        opportunities, library, clip_duration=clip_duration, max_overlays=max_overlays
    )


def load_broll_library(manifest_path: Optional[Path] = None) -> BrollLibrary:
    """Charge les assets B-roll embarques depuis leur manifest versionne.

    Un manifeste illisible, un fichier absent ou un sha256 qui ne correspond
    plus ecarte l'asset concerne avec un warning : un bundle incomplet doit
    degrader vers le blur cinematique, jamais faire echouer un rendu.
    Un asset sans preuve de licence est refuse par ``load_manifest``.
    """
    path = Path(manifest_path) if manifest_path else manifest_path_for("broll")
    try:
        manifest = load_manifest(path)
    except ManifestError as exc:
        logger.warning("B-roll off: manifest inutilisable (%s)", exc)
        return BrollLibrary()

    # Les chemins du manifest sont relatifs a sa racine versionnee
    # (<racine>/broll/manifest.json -> <racine>), comme pour l'audio.
    root = path.resolve().parent.parent
    assets: List[BrollAsset] = []
    for entry in manifest["assets"]:
        asset_path = root / entry["path"]
        if not asset_path.is_file():
            logger.warning(
                "B-roll: asset %s absent (%s), ignore", entry["id"], asset_path
            )
            continue
        asset = BrollAsset(
            id=entry["id"],
            path=asset_path,
            duration=float(entry["duration"]),
            license=entry["license"],
            source_url=entry["source_url"],
            published_at=entry["published_at"],
            sha256=entry["sha256"],
            keywords=tuple(entry.get("keywords") or ()),
        )
        if not asset.integrity_ok():
            logger.warning(
                "B-roll: sha256 de %s different du manifest, ignore", entry["id"]
            )
            continue
        assets.append(asset)
    return BrollLibrary(assets)


def apply_local_broll(
    clip_path: Path,
    plan: BrollPlan,
    library: BrollLibrary,
    output_path: Path,
) -> bool:
    """Incruste le plan B-roll local sur un clip 1080x1920.

    Retourne True si au moins un overlay a ete applique, False sinon
    (le clip d'origine — deja en blur cinematique — est alors inchange :
    jamais de trou visuel). Zero appel reseau : seuls des fichiers du
    bundle versionne sont lus.
    """
    # Import tardif : video_utils tire tout le pipeline ; ce module reste
    # importable sans ffmpeg pour les tests purs du resolveur.
    from ..video_utils import insert_broll_into_clip

    if not plan.cues:
        return False
    ordered = sorted(plan.cues, key=lambda cue: cue.at, reverse=True)
    current = Path(clip_path)
    if not current.is_file():
        return False
    applied = 0
    temp_outputs: List[Path] = []
    for index, cue in enumerate(ordered):
        asset = library.asset_by_id(cue.asset_id)
        if asset is None or not asset.path.is_file():
            continue
        is_last = index == len(ordered) - 1
        target = Path(output_path) if is_last else Path(output_path).parent / (
            f"{Path(output_path).stem}.broll_tmp_{index}.mp4"
        )
        if not is_last:
            temp_outputs.append(target)
        ok = insert_broll_into_clip(
            current, asset.path, cue.at, cue.duration, target
        )
        if ok:
            current = target
            applied += 1
    for temp in temp_outputs:
        try:
            if temp.exists() and temp.resolve() != Path(output_path).resolve():
                temp.unlink()
        except OSError:
            pass
    return applied > 0
