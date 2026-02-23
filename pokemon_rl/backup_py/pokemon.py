"""
pokemon.py — 포켓몬 / 기술 클래스
"""
from __future__ import annotations
import copy
import math
from dataclasses import dataclass, field
from typing import Optional

# ── 스탯 랭크 보정 테이블 ──────────────────────────────
STAT_STAGE_MULT = {
    -6: 2/8, -5: 2/7, -4: 2/6, -3: 2/5, -2: 2/4, -1: 2/3,
     0: 1.0,
     1: 3/2,  2: 4/2,  3: 5/2,  4: 6/2,  5: 7/2,  6: 8/2,
}

STATUS_LIST = ["burn", "paralysis", "sleep", "freeze", "poison", "toxic", "none"]


@dataclass
class Move:
    name: str
    type_: str
    category: str          # "physical" | "special" | "status"
    power: int             # 위력 (변화기술 0)
    accuracy: int          # 명중률 (0 = 반드시 명중)
    pp: int
    max_pp: int = 0
    priority: int = 0      # 우선도
    effect: str = ""       # "burn", "paralyze", "flinch", "boost_atk" 등
    effect_chance: int = 0 # 부가효과 발동 확률 (%)
    target: str = "opponent"  # "opponent" | "self" | "all"
    crit_stage: int = 0

    def __post_init__(self):
        if self.max_pp == 0:
            self.max_pp = self.pp

    @property
    def current_pp(self):
        return self.pp

    def use(self):
        """PP 소비"""
        if self.pp > 0:
            self.pp -= 1

    def clone(self) -> "Move":
        return copy.deepcopy(self)


@dataclass
class Pokemon:
    name: str
    types: list[str]
    level: int

    # 기저 스탯
    base_hp: int
    base_attack: int
    base_defense: int
    base_sp_attack: int
    base_sp_defense: int
    base_speed: int

    # 개체값 (기본 31)
    iv_hp: int = 31
    iv_attack: int = 31
    iv_defense: int = 31
    iv_sp_attack: int = 31
    iv_sp_defense: int = 31
    iv_speed: int = 31

    # 노력치 (기본 0)
    ev_hp: int = 0
    ev_attack: int = 0
    ev_defense: int = 0
    ev_sp_attack: int = 0
    ev_sp_defense: int = 0
    ev_speed: int = 0

    nature_mod: dict = field(default_factory=dict)  # {"attack": 1.1, "speed": 0.9}
    moves: list[Move] = field(default_factory=list)
    ability: str = ""
    item: str = ""

    # 배틀 중 상태 (초기화 후 설정)
    current_hp: int = 0
    status: str = "none"       # 주요 상태이상
    confusion: bool = False
    flinched: bool = False

    # 스탯 랭크 (-6 ~ +6)
    rank_attack: int = 0
    rank_defense: int = 0
    rank_sp_attack: int = 0
    rank_sp_defense: int = 0
    rank_speed: int = 0
    rank_accuracy: int = 0
    rank_evasion: int = 0

    # 독 카운터 (맹독용)
    toxic_counter: int = 0
    sleep_turns: int = 0

    def __post_init__(self):
        if self.current_hp == 0:
            self.current_hp = self.max_hp

    # ── 실제 스탯 계산 ──────────────────────────────────
    def _calc_stat(self, base: int, iv: int, ev: int, nature: float = 1.0) -> int:
        return math.floor(
            (math.floor((2 * base + iv + math.floor(ev / 4)) * self.level / 100) + 5)
            * nature
        )

    @property
    def max_hp(self) -> int:
        return math.floor(
            (2 * self.base_hp + self.iv_hp + math.floor(self.ev_hp / 4))
            * self.level / 100
        ) + self.level + 10

    @property
    def attack(self) -> int:
        return self._calc_stat(self.base_attack, self.iv_attack, self.ev_attack,
                               self.nature_mod.get("attack", 1.0))

    @property
    def defense(self) -> int:
        return self._calc_stat(self.base_defense, self.iv_defense, self.ev_defense,
                               self.nature_mod.get("defense", 1.0))

    @property
    def sp_attack(self) -> int:
        return self._calc_stat(self.base_sp_attack, self.iv_sp_attack, self.ev_sp_attack,
                               self.nature_mod.get("sp_attack", 1.0))

    @property
    def sp_defense(self) -> int:
        return self._calc_stat(self.base_sp_defense, self.iv_sp_defense, self.ev_sp_defense,
                               self.nature_mod.get("sp_defense", 1.0))

    @property
    def speed(self) -> int:
        base_speed = self._calc_stat(self.base_speed, self.iv_speed, self.ev_speed,
                                     self.nature_mod.get("speed", 1.0))
        # 마비 시 속도 절반
        if self.status == "paralysis":
            base_speed = math.floor(base_speed * 0.5)
        return base_speed

    def effective_stat(self, stat_name: str) -> int:
        """랭크 보정이 반영된 실질 스탯"""
        base = getattr(self, stat_name)
        rank_map = {
            "attack": self.rank_attack,
            "defense": self.rank_defense,
            "sp_attack": self.rank_sp_attack,
            "sp_defense": self.rank_sp_defense,
            "speed": self.rank_speed,
        }
        rank = rank_map.get(stat_name, 0)
        return math.floor(base * STAT_STAGE_MULT[rank])

    # ── 상태 확인 ────────────────────────────────────────
    @property
    def is_fainted(self) -> bool:
        return self.current_hp <= 0

    @property
    def hp_ratio(self) -> float:
        return self.current_hp / self.max_hp

    def take_damage(self, damage: int):
        self.current_hp = max(0, self.current_hp - damage)

    def heal(self, amount: int):
        self.current_hp = min(self.max_hp, self.current_hp + amount)

    def apply_status(self, status: str) -> bool:
        """상태이상 부여 (이미 있으면 실패)"""
        if self.status != "none":
            return False
        self.status = status
        if status == "sleep":
            import random
            self.sleep_turns = random.randint(1, 3)
        return True

    def change_rank(self, stat: str, delta: int) -> str:
        """스탯 랭크 변화, 결과 메시지 반환"""
        attr = f"rank_{stat}"
        current = getattr(self, attr)
        new = max(-6, min(6, current + delta))
        setattr(self, attr, new)
        if new == current:
            return "more" if delta > 0 else "less"
        return "up" if delta > 0 else "down"

    def reset_ranks(self):
        for attr in ["rank_attack","rank_defense","rank_sp_attack",
                     "rank_sp_defense","rank_speed","rank_accuracy","rank_evasion"]:
            setattr(self, attr, 0)

    def available_moves(self) -> list[Move]:
        return [m for m in self.moves if m.pp > 0]

    def to_obs_vector(self) -> list[float]:
        """관측 벡터 변환 (신경망 입력용)"""
        from env.damage_calc import ALL_TYPES
        type_one_hot = [1.0 if t in self.types else 0.0 for t in ALL_TYPES]
        return [
            self.hp_ratio,
            self.effective_stat("attack") / 500,
            self.effective_stat("defense") / 500,
            self.effective_stat("sp_attack") / 500,
            self.effective_stat("sp_defense") / 500,
            self.effective_stat("speed") / 500,
            self.rank_attack / 6,
            self.rank_defense / 6,
            self.rank_sp_attack / 6,
            self.rank_sp_defense / 6,
            self.rank_speed / 6,
            STATUS_LIST.index(self.status) / len(STATUS_LIST),
            1.0 if self.is_fainted else 0.0,
        ] + type_one_hot  # 총 13 + 18 = 31 차원

    def clone(self) -> "Pokemon":
        p = copy.deepcopy(self)
        return p

    def __repr__(self):
        return f"Pokemon({self.name}, HP={self.current_hp}/{self.max_hp}, Status={self.status})"
