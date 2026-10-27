"""Deterministic 30-species builds and disjoint team-composition partitions.

Species and move vocabularies are shared. Three-member species combinations,
held items/EV builds, and episode seeds are held out separately.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
from pathlib import Path
import random

DATASET_VERSION = "roguelike30-threeway-v1"
SPLITS = ("train", "validation", "test")
ROOT = Path(__file__).resolve().parents[1]
PHYSICAL = frozenset(("Gyarados", "Arcanine", "Talonflame", "Breloom", "Ferrothorn",
                      "Scizor", "Metagross", "Mimikyu", "Garchomp", "Dragonite",
                      "Tyranitar", "Excadrill", "Lucario", "Snorlax"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def build_fingerprint(build):
    # A permutation of the same move slots is still the same build.
    value = {key: copy.deepcopy(build.get(key)) for key in ("species", "ability", "item", "nature", "level", "evs", "ivs")}
    value["moves"] = sorted(build.get("moves", []))
    return digest(value)


def team_fingerprint(team):
    """Ignore party order: a reordered composition is not an unseen team."""
    return digest(sorted(p["species"] for p in team))


def composition_split(species):
    bucket = int(digest([DATASET_VERSION, sorted(species)]), 16) % 20
    return "train" if bucket < 14 else "validation" if bucket < 17 else "test"


def normalize_build(build):
    """Assign offense and nature to the curated build's actual move role."""
    result = copy.deepcopy(build)
    physical = result["species"] in PHYSICAL
    offense = "atk" if physical else "spa"
    fast = (result.get("evs") or {}).get("spe", 0) >= 128
    result["evs"] = {offense: 252, "spe" if fast else "hp": 252, "hp" if fast else "def": 4}
    result["nature"] = ("Jolly" if physical else "Timid") if fast else ("Adamant" if physical else "Modest")
    result["level"] = 50
    return result


class TeamDataset:
    def __init__(self, split="train", *, pool=None):
        if split not in SPLITS:
            raise ValueError("Dataset split must be train, validation or test")
        if pool is None:
            pool = json.loads((ROOT / "content/roguelike.json").read_text(encoding="utf-8"))["recruit_pool"]
        self.source = copy.deepcopy(pool)
        self.split = split
        self.species = tuple(sorted(p["species"] for p in pool))
        if len(self.species) != 30 or len(set(self.species)) != 30:
            raise ValueError("This dataset contract requires exactly 30 distinct curated species")
        self.builds = {}
        for raw in pool:
            build = normalize_build(raw)
            offense = "atk" if build["species"] in PHYSICAL else "spa"
            fast = build["evs"].get("spe", 0) >= 128
            if split == "validation":
                build["evs"] = {offense: 252, "spe" if fast else "hp": 220, "hp" if fast else "def": 36}
                if build["species"] != "Pikachu":
                    build["item"] = "Sitrus Berry"
            elif split == "test":
                build["evs"] = {offense: 252, "spe" if fast else "hp": 196, "hp" if fast else "spd": 60}
                if build["species"] != "Pikachu":
                    build["item"] = "Lum Berry"
            self.builds[build["species"]] = build
        self.compositions = tuple(combo for combo in itertools.combinations(self.species, 3) if composition_split(combo) == split)
        if not self.compositions:
            raise ValueError("Dataset partition has no legal team compositions")

    def teams(self, seed):
        rng = random.Random(seed)
        result = {}
        for side in ("p1", "p2"):
            team = [copy.deepcopy(self.builds[name]) for name in rng.choice(self.compositions)]
            rng.shuffle(team)
            for build in team:
                rng.shuffle(build["moves"])
            result[side] = team
        return result

    def manifest(self):
        return {"version": DATASET_VERSION, "split": self.split, "species_count": len(self.species),
                "source_sha256": digest(self.source), "build_ids": sorted(build_fingerprint(p) for p in self.builds.values()),
                "composition_count": len(self.compositions), "composition_sha256": digest(self.compositions),
                "partition": "sha256(version, sorted species combination) modulo 20: train 0-13, validation 14-16, test 17-19",
                "shared": "Species, move vocabulary and battle rules are shared; this is held-out build/composition evaluation, not unseen-species evaluation."}


def ensure_disjoint_training_data(policy, evaluation):
    """A checkpoint trained on any evaluated partition/build must not pass."""
    report = evaluation.manifest()
    if report["split"] == "train":
        raise ValueError("Evaluation requires a held-out validation or test partition")
    for trained in getattr(policy, "training_data_contracts", []):
        if trained.get("version") != report["version"]:
            raise ValueError("Training dataset contract differs; held-out relationship cannot be verified")
        if trained.get("source_sha256") != report["source_sha256"]:
            raise ValueError("Training content differs; held-out relationship cannot be verified")
        if trained.get("split") != "train" or set(trained.get("build_ids", [])) & set(report["build_ids"]):
            raise ValueError("Evaluation dataset overlaps this checkpoint's training builds or team partition")
        if trained != TeamDataset("train", pool=evaluation.source).manifest():
            raise ValueError("Training dataset provenance is incomplete or differs from the declared partition")
