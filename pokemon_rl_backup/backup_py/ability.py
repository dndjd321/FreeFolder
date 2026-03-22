"""
env/ability.py — 포켓몬 특성(어빌리티) 시스템

지원 특성 목록:
  공격 강화: 맹화/격류/과부하/무기력/초식/전기엔진/기세등등/적응력/의기양양
  방어:      두꺼운지방/수의베일/천연/마이페이스/분노/수분/건조피부/매직가드
  스탯 변화: 위협/느릿느릿/엽록소/쓱쓱/모래날리기의력/특성 시너지 다수
  날씨:      모래날리기/눈퍼붓기/가뭄/물을머금다
  기타:      부유/방진/잘자는체질/빠른발/오기
"""
from __future__ import annotations
from typing import TYPE_CHECKING
import random

if TYPE_CHECKING:
    from env.pokemon import Pokemon
    from env.battle_env import PokemonBattleEnv


# ── 특성 트리거 타입 ──────────────────────────────────────
TRIGGER_SWITCH_IN   = "on_switch_in"    # 등장 시
TRIGGER_BEFORE_MOVE = "before_move"     # 기술 사용 전
TRIGGER_ON_HIT      = "on_hit"          # 피격 시
TRIGGER_END_TURN    = "end_of_turn"     # 턴 종료
TRIGGER_ON_ATTACK   = "on_attack"       # 공격 시
TRIGGER_MODIFY_DMG  = "modify_damage"   # 데미지 계산 보정


class Ability:
    """특성 기본 클래스"""
    name: str = "none"
    description: str = ""

    def on_switch_in(self, owner: "Pokemon", env: "PokemonBattleEnv", log: list) -> float:
        return 0.0

    def before_move(self, owner: "Pokemon", move, env: "PokemonBattleEnv", log: list) -> bool:
        """False 반환 시 기술 사용 불가"""
        return True

    def modify_damage(self, owner: "Pokemon", move, base_damage: int,
                      is_attacker: bool, env: "PokemonBattleEnv") -> float:
        """데미지 배율 반환 (1.0 = 변화 없음)"""
        return 1.0

    def on_hit(self, owner: "Pokemon", attacker: "Pokemon", move,
               damage: int, env: "PokemonBattleEnv", log: list) -> float:
        return 0.0

    def on_attack(self, owner: "Pokemon", defender: "Pokemon", move,
                  damage: int, env: "PokemonBattleEnv", log: list) -> float:
        return 0.0

    def end_of_turn(self, owner: "Pokemon", env: "PokemonBattleEnv", log: list) -> float:
        return 0.0


# ══════════════════════════════════════════════════════════
# 구체적 특성 구현
# ══════════════════════════════════════════════════════════

class Blaze(Ability):
    """맹화 — HP 1/3 이하에서 불꽃 기술 1.5배"""
    name = "blaze"
    description = "HP 1/3 이하에서 불꽃 기술의 위력이 1.5배"

    def modify_damage(self, owner, move, base_damage, is_attacker, env):
        if is_attacker and move.type_ == "fire" and owner.hp_ratio <= 1/3:
            return 1.5
        return 1.0


class Torrent(Ability):
    """격류 — HP 1/3 이하에서 물 기술 1.5배"""
    name = "torrent"
    description = "HP 1/3 이하에서 물 기술의 위력이 1.5배"

    def modify_damage(self, owner, move, base_damage, is_attacker, env):
        if is_attacker and move.type_ == "water" and owner.hp_ratio <= 1/3:
            return 1.5
        return 1.0


class Overgrow(Ability):
    """과부하 — HP 1/3 이하에서 풀 기술 1.5배"""
    name = "overgrow"
    def modify_damage(self, owner, move, base_damage, is_attacker, env):
        if is_attacker and move.type_ == "grass" and owner.hp_ratio <= 1/3:
            return 1.5
        return 1.0


class SwiftSwim(Ability):
    """쓱쓱 — 비 날씨에서 스피드 2배"""
    name = "swift-swim"
    def on_switch_in(self, owner, env, log):
        if env.weather == "rain":
            log.append(f"  {owner.name}의 쓱쓱으로 속도가 2배!")
        return 0.0

    def get_speed_modifier(self, owner, env) -> float:
        return 2.0 if env.weather == "rain" else 1.0


class Chlorophyll(Ability):
    """엽록소 — 쾌청 날씨에서 스피드 2배"""
    name = "chlorophyll"
    def get_speed_modifier(self, owner, env) -> float:
        return 2.0 if env.weather == "sun" else 1.0


class Intimidate(Ability):
    """위협 — 등장 시 상대 공격 1단계 하락"""
    name = "intimidate"
    description = "등장 시 상대의 공격을 1단계 낮춘다"

    def on_switch_in(self, owner, env, log):
        # 내가 플레이어면 상대에게, 상대면 플레이어에게 적용
        if env.player_team[env.player_active_idx] is owner:
            target = env.opponent_active
        else:
            target = env.player_active

        result = target.change_rank("attack", -1)
        if result == "down":
            log.append(f"  {owner.name}의 위협으로 {target.name}의 공격이 떨어졌다!")
            return 0.3
        return 0.0


class ThickFat(Ability):
    """두꺼운지방 — 불꽃/얼음 기술 데미지 절반"""
    name = "thick-fat"
    def modify_damage(self, owner, move, base_damage, is_attacker, env):
        if not is_attacker and move.type_ in ("fire", "ice"):
            return 0.5
        return 1.0


class WaterAbsorb(Ability):
    """저수 — 물 기술에 무효, HP 회복"""
    name = "water-absorb"
    def on_hit(self, owner, attacker, move, damage, env, log):
        if move.type_ == "water":
            heal = owner.max_hp // 4
            owner.heal(heal)
            owner.current_hp += damage  # 데미지 취소 (사전에 입혀진 것 복구)
            owner.current_hp = min(owner.max_hp, owner.current_hp)
            log.append(f"  {owner.name}의 저수로 HP를 {heal} 회복!")
            return 0.3
        return 0.0


class VoltAbsorb(Ability):
    """축전 — 전기 기술에 무효, HP 회복"""
    name = "volt-absorb"
    def on_hit(self, owner, attacker, move, damage, env, log):
        if move.type_ == "electric":
            heal = owner.max_hp // 4
            owner.heal(heal + damage)
            owner.current_hp = min(owner.max_hp, owner.current_hp)
            log.append(f"  {owner.name}의 축전으로 HP를 {heal} 회복!")
            return 0.3
        return 0.0


class Levitate(Ability):
    """부유 — 땅 기술에 무효"""
    name = "levitate"
    def modify_damage(self, owner, move, base_damage, is_attacker, env):
        if not is_attacker and move.type_ == "ground":
            return 0.0  # 무효
        return 1.0


class MagicGuard(Ability):
    """매직가드 — 직접 공격 외 데미지 무효 (독/화상/반동 등)"""
    name = "magic-guard"
    def end_of_turn(self, owner, env, log):
        # 턴 종료 데미지 취소 — 이 특성은 battle_env에서 체크 필요
        return 0.0


class Guts(Ability):
    """근성 — 상태이상 시 공격 1.5배"""
    name = "guts"
    def modify_damage(self, owner, move, base_damage, is_attacker, env):
        if is_attacker and move.category == "physical" and owner.status != "none":
            return 1.5
        return 1.0


class SpeedBoost(Ability):
    """가속 — 턴 종료마다 스피드 1단계 상승"""
    name = "speed-boost"
    def end_of_turn(self, owner, env, log):
        result = owner.change_rank("speed", 1)
        if result == "up":
            log.append(f"  {owner.name}의 가속으로 스피드가 올라갔다!")
            return 0.1
        return 0.0


class Moxie(Ability):
    """의기양양 — 상대를 쓰러뜨리면 공격 1단계 상승"""
    name = "moxie"
    def on_attack(self, owner, defender, move, damage, env, log):
        if defender.is_fainted:
            result = owner.change_rank("attack", 1)
            if result == "up":
                log.append(f"  {owner.name}의 의기양양으로 공격이 올라갔다!")
                return 0.2
        return 0.0


class Adaptability(Ability):
    """적응력 — STAB 보너스가 1.5배 → 2배"""
    name = "adaptability"
    def modify_damage(self, owner, move, base_damage, is_attacker, env):
        if is_attacker and move.type_ in owner.types:
            # 기본 STAB(1.5)를 2배로 올리는 추가 보정
            return 2.0 / 1.5
        return 1.0


class Regenerator(Ability):
    """재생력 — 교체 시 HP 1/3 회복"""
    name = "regenerator"
    # 교체 시 처리는 battle_env에서 on_switch_out 호출


class RoughSkin(Ability):
    """껍질갑옷 / 철가시 — 접촉 기술로 공격한 상대에게 반동"""
    name = "rough-skin"
    def on_hit(self, owner, attacker, move, damage, env, log):
        # 접촉기술 판정 (물리기술이면 대부분 접촉)
        if move.category == "physical":
            recoil = max(1, attacker.max_hp // 8)
            attacker.take_damage(recoil)
            log.append(f"  {owner.name}의 껍질갑옷으로 {attacker.name}이(가) {recoil} 반동 피해!")
        return 0.0


class FlashFire(Ability):
    """불꽃몸 — 불꽃 기술에 무효, 불꽃 기술 1.5배"""
    name = "flash-fire"
    _activated: bool = False

    def on_hit(self, owner, attacker, move, damage, env, log):
        if move.type_ == "fire":
            owner.current_hp += damage  # 데미지 취소
            owner.current_hp = min(owner.max_hp, owner.current_hp)
            self._activated = True
            log.append(f"  {owner.name}의 불꽃몸이 발동! 불꽃 기술이 강해졌다!")
        return 0.0

    def modify_damage(self, owner, move, base_damage, is_attacker, env):
        if is_attacker and move.type_ == "fire" and self._activated:
            return 1.5
        return 1.0


class Hustle(Ability):
    """기세 — 물리공격 1.5배, 명중률 0.8배"""
    name = "hustle"
    def modify_damage(self, owner, move, base_damage, is_attacker, env):
        if is_attacker and move.category == "physical":
            return 1.5
        return 1.0


class NaturalCure(Ability):
    """자연회복 — 교체 시 상태이상 회복"""
    name = "natural-cure"
    # 교체 시 처리는 battle_env에서 on_switch_out 호출


class SandStream(Ability):
    """모래날리기 — 등장 시 모래바람 발생"""
    name = "sand-stream"
    def on_switch_in(self, owner, env, log):
        if env.weather != "sandstorm":
            env.weather = "sandstorm"
            env.weather_turns = 5
            log.append(f"  {owner.name}의 모래날리기로 모래바람이 불기 시작했다!")
        return 0.0


class Drought(Ability):
    """가뭄 — 등장 시 쾌청 발생"""
    name = "drought"
    def on_switch_in(self, owner, env, log):
        if env.weather != "sun":
            env.weather = "sun"
            env.weather_turns = 5
            log.append(f"  {owner.name}의 가뭄으로 날씨가 맑아졌다!")
        return 0.0


class Drizzle(Ability):
    """우천 — 등장 시 비 발생"""
    name = "drizzle"
    def on_switch_in(self, owner, env, log):
        if env.weather != "rain":
            env.weather = "rain"
            env.weather_turns = 5
            log.append(f"  {owner.name}의 우천으로 비가 내리기 시작했다!")
        return 0.0


# ══════════════════════════════════════════════════════════
# 특성 레지스트리
# ══════════════════════════════════════════════════════════

ABILITY_REGISTRY: dict[str, Ability] = {
    "blaze": Blaze(),
    "torrent": Torrent(),
    "overgrow": Overgrow(),
    "swift-swim": SwiftSwim(),
    "chlorophyll": Chlorophyll(),
    "intimidate": Intimidate(),
    "thick-fat": ThickFat(),
    "water-absorb": WaterAbsorb(),
    "volt-absorb": VoltAbsorb(),
    "levitate": Levitate(),
    "magic-guard": MagicGuard(),
    "guts": Guts(),
    "speed-boost": SpeedBoost(),
    "moxie": Moxie(),
    "adaptability": Adaptability(),
    "regenerator": Regenerator(),
    "rough-skin": RoughSkin(),
    "flash-fire": FlashFire(),
    "hustle": Hustle(),
    "natural-cure": NaturalCure(),
    "sand-stream": SandStream(),
    "drought": Drought(),
    "drizzle": Drizzle(),
    "none": Ability(),
}


def get_ability(name: str) -> Ability:
    """특성 이름으로 특성 객체 반환 (없으면 기본)"""
    return ABILITY_REGISTRY.get(name.lower().replace(" ", "-"), Ability())


def list_abilities() -> list[str]:
    return list(ABILITY_REGISTRY.keys())
