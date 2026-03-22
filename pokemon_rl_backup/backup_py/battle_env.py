"""
battle_env.py — 포켓몬 싱글 배틀 Gymnasium 환경 v2
특성(Ability) + 날씨(Weather) + 지형(Terrain) 완전 통합 버전
"""
from __future__ import annotations

import random
import copy
import numpy as np
import gymnasium as gym
from gymnasium import spaces

from env.pokemon import Pokemon, Move
from env.damage_calc import calc_damage, calc_critical_chance, get_type_multiplier
from env.ability import get_ability, Ability
from env.weather import (
    get_weather_move_modifier, get_weather_accuracy_modifier,
    apply_weather_end_of_turn,
    WEATHER_START_MSG, WEATHER_END_MSG,
    get_terrain,
)


# ══════════════════════════════════════════════════════════
# 포켓몬 풀 (특성 포함)
# ══════════════════════════════════════════════════════════

def _poke(name_ko, types, level, hp, atk, def_, sp_atk, sp_def, spd, moves, ability="none"):
    p = Pokemon(
        name=name_ko, types=types, level=level,
        base_hp=hp, base_attack=atk, base_defense=def_,
        base_sp_attack=sp_atk, base_sp_defense=sp_def, base_speed=spd,
        moves=moves, ability=ability,
    )
    p.ability_obj = get_ability(ability)
    p.ability_name = ability
    return p


def make_sample_pokemon_pool() -> list[Pokemon]:
    return [
        _poke("리자몽", ["fire","flying"], 50, 78, 84, 78, 109, 85, 100, ability="blaze", moves=[
            Move("화염방사","fire","special",90,100,15,effect="burn",effect_chance=10),
            Move("에어슬래시","flying","special",75,95,15,effect="flinch",effect_chance=30),
            Move("드래곤클로","dragon","physical",80,100,15),
            Move("칼춤","normal","status",0,0,20,effect="boost_attack_2",target="self"),
        ]),
        _poke("거북왕", ["water"], 50, 79, 83, 100, 85, 105, 78, ability="torrent", moves=[
            Move("파도타기","water","special",90,100,15),
            Move("눈보라","ice","special",110,70,10,effect="freeze",effect_chance=10),
            Move("철벽","steel","status",0,0,15,effect="boost_defense_2",target="self"),
            Move("냉동빔","ice","special",90,100,10,effect="freeze",effect_chance=10),
        ]),
        _poke("이상해꽃", ["grass","poison"], 50, 80, 82, 83, 100, 100, 80, ability="overgrow", moves=[
            Move("에너지볼","grass","special",90,100,10,effect="drop_sp_def",effect_chance=10),
            Move("독가루","poison","status",0,100,35,effect="poison",target="opponent"),
            Move("수면가루","grass","status",0,75,15,effect="sleep",target="opponent"),
            Move("합성","normal","status",0,0,5,effect="heal_half",target="self"),
        ]),
        _poke("팬텀", ["ghost","poison"], 50, 60, 65, 60, 130, 75, 110, ability="levitate", moves=[
            Move("섀도볼","ghost","special",80,100,15,effect="drop_sp_def",effect_chance=20),
            Move("사이코키네시스","psychic","special",90,100,10,effect="drop_sp_def",effect_chance=10),
            Move("도깨비불","fire","status",0,85,15,effect="burn",target="opponent"),
            Move("최면술","psychic","status",0,60,20,effect="sleep",target="opponent"),
        ]),
        _poke("괴력몬", ["fighting"], 50, 90, 130, 80, 65, 85, 55, ability="guts", moves=[
            Move("크로스촙","fighting","physical",100,80,5,crit_stage=1),
            Move("지진","ground","physical",100,100,10),
            Move("스톤에지","rock","physical",100,80,5,crit_stage=1),
            Move("불꽃펀치","fire","physical",75,100,15,effect="burn",effect_chance=10),
        ]),
        _poke("라프라스", ["water","ice"], 50, 130, 85, 80, 85, 95, 60, ability="water-absorb", moves=[
            Move("냉동빔","ice","special",90,100,10,effect="freeze",effect_chance=10),
            Move("파도타기","water","special",90,100,15),
            Move("썬더볼트","electric","special",90,100,15,effect="paralyze",effect_chance=10),
            Move("노래","normal","status",0,55,15,effect="sleep",target="opponent"),
        ]),
        _poke("한카리아스", ["dragon","ground"], 50, 108, 130, 95, 80, 85, 102, ability="rough-skin", moves=[
            Move("역린","dragon","physical",120,100,10),
            Move("지진","ground","physical",100,100,10),
            Move("불꽃엄니","fire","physical",65,95,15,effect="burn",effect_chance=10),
            Move("스톤에지","rock","physical",100,80,5,crit_stage=1),
        ]),
        _poke("루카리오", ["fighting","steel"], 50, 70, 110, 70, 115, 70, 90, ability="adaptability", moves=[
            Move("파동탄","fighting","special",80,100,10),
            Move("섀도볼","ghost","special",80,100,15),
            Move("번개펀치","electric","physical",75,100,15,effect="paralyze",effect_chance=10),
            Move("나쁜음모","dark","status",0,0,20,effect="boost_sp_attack_2",target="self"),
        ]),
        _poke("보만다", ["dragon","flying"], 50, 95, 135, 80, 110, 80, 100, ability="moxie", moves=[
            Move("드래곤클로","dragon","physical",80,100,15),
            Move("지진","ground","physical",100,100,10),
            Move("불꽃방사","fire","special",90,100,15,effect="burn",effect_chance=10),
            Move("날개치기","flying","physical",60,100,35),
        ]),
        _poke("잠만보", ["normal"], 50, 160, 110, 65, 65, 110, 30, ability="thick-fat", moves=[
            Move("하품","normal","status",0,0,10,effect="sleep",target="opponent"),
            Move("잠재파워","normal","physical",60,100,15),
            Move("지진","ground","physical",100,100,10),
            Move("몸통박치기","normal","physical",120,85,10),
        ]),
    ]


def make_team(pool: list[Pokemon], size: int = 3) -> list[Pokemon]:
    selected = random.sample(pool, min(size, len(pool)))
    return [copy.deepcopy(p) for p in selected]


# ══════════════════════════════════════════════════════════
# 배틀 환경
# ══════════════════════════════════════════════════════════

WEATHERS = ["none","rain","sun","sandstorm","hail","snow"]
TERRAINS = ["none","electric","grassy","misty","psychic"]


class PokemonBattleEnv(gym.Env):
    metadata = {"render_modes": ["ansi"]}

    def __init__(self, team_size: int = 3, max_turns: int = 100,
                 render_mode: str | None = None):
        super().__init__()
        self.team_size = team_size
        self.max_turns = max_turns
        self.render_mode = render_mode
        self.pokemon_pool = make_sample_pokemon_pool()

        self.weather: str = "none"
        self.weather_turns: int = 0
        self.terrain: str = "none"
        self.terrain_turns: int = 0

        # 관측 차원: 포켓몬 벡터×2 + 파티 HP×2 + PP×4 + 턴 + 날씨OH + 지형OH
        pokemon_obs_dim = 31
        obs_dim = (pokemon_obs_dim * 2 + team_size * 2
                   + 4 + 1 + len(WEATHERS) + len(TERRAINS))

        self.observation_space = spaces.Box(
            low=-1.0, high=1.0, shape=(obs_dim,), dtype=np.float32
        )
        self.action_space = spaces.Discrete(4 + (team_size - 1))

        self.player_team: list[Pokemon] = []
        self.opponent_team: list[Pokemon] = []
        self.player_active_idx: int = 0
        self.opponent_active_idx: int = 0
        self.turn: int = 0
        self.battle_log: list[str] = []

    @property
    def player_active(self): return self.player_team[self.player_active_idx]
    @property
    def opponent_active(self): return self.opponent_team[self.opponent_active_idx]
    def _alive(self, team): return [i for i, p in enumerate(team) if not p.is_fainted]
    def _get_ability(self, pokemon): return getattr(pokemon, "ability_obj", get_ability("none"))

    def _get_obs(self):
        weather_oh = [1.0 if self.weather == w else 0.0 for w in WEATHERS]
        terrain_oh = [1.0 if self.terrain == t else 0.0 for t in TERRAINS]
        own_vec = self.player_active.to_obs_vector()
        own_hp  = [p.hp_ratio for p in self.player_team]
        opp_vec = self.opponent_active.to_obs_vector()
        opp_alive = [0.0 if p.is_fainted else 1.0 for p in self.opponent_team]
        move_pp = [m.pp/m.max_pp if m.max_pp else 0.0 for m in self.player_active.moves[:4]]
        while len(move_pp) < 4: move_pp.append(0.0)
        return np.array(
            own_vec + own_hp + opp_vec + opp_alive
            + move_pp + [self.turn/self.max_turns] + weather_oh + terrain_oh,
            dtype=np.float32
        )

    def _get_info(self):
        return {"turn": self.turn, "weather": self.weather, "terrain": self.terrain,
                "player_active": self.player_active.name,
                "opponent_active": self.opponent_active.name}

    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.player_team = make_team(self.pokemon_pool, self.team_size)
        self.opponent_team = make_team(self.pokemon_pool, self.team_size)
        self.player_active_idx = 0
        self.opponent_active_idx = 0
        self.turn = 0
        self.weather = "none"
        self.weather_turns = 0
        self.terrain = "none"
        self.terrain_turns = 0
        self.battle_log = []
        log = []
        self._trigger_switch_in(self.player_active, log)
        self._trigger_switch_in(self.opponent_active, log)
        self.battle_log.extend(log)
        return self._get_obs(), self._get_info()

    def step(self, action: int):
        self.turn += 1
        reward = 0.0
        terminated = False
        log = []

        p_move_idx, p_switch_idx = self._parse_action(action, self.player_team, self.player_active_idx)
        opp_action = self._opponent_policy()
        o_move_idx, o_switch_idx = self._parse_action(opp_action, self.opponent_team, self.opponent_active_idx)

        # 교체 처리
        if p_switch_idx is not None:
            r, l = self._do_switch("player", p_switch_idx); reward += r; log += l
        if o_switch_idx is not None:
            _, l = self._do_switch("opponent", o_switch_idx); log += l

        # 기술 처리
        if p_move_idx is not None and o_move_idx is not None:
            pm = self._get_move(self.player_active, p_move_idx)
            om = self._get_move(self.opponent_active, o_move_idx)
            if self._goes_first(self.player_active, pm, self.opponent_active, om):
                r,t,l = self._execute_move(self.player_active, pm, self.opponent_active, "플레이어", True)
                reward+=r; terminated|=t; log+=l
                if not self.opponent_active.is_fainted:
                    r,t,l = self._execute_move(self.opponent_active, om, self.player_active, "상대", False)
                    reward-=r; terminated|=t; log+=l
            else:
                r,t,l = self._execute_move(self.opponent_active, om, self.player_active, "상대", False)
                reward-=r; terminated|=t; log+=l
                if not self.player_active.is_fainted:
                    r,t,l = self._execute_move(self.player_active, pm, self.opponent_active, "플레이어", True)
                    reward+=r; terminated|=t; log+=l
        elif p_move_idx is not None:
            pm = self._get_move(self.player_active, p_move_idx)
            r,t,l = self._execute_move(self.player_active, pm, self.opponent_active, "플레이어", True)
            reward+=r; terminated|=t; log+=l
        elif o_move_idx is not None:
            om = self._get_move(self.opponent_active, o_move_idx)
            r,t,l = self._execute_move(self.opponent_active, om, self.player_active, "상대", False)
            reward-=r; terminated|=t; log+=l

        # 턴 종료
        r, l = self._end_of_turn(); reward += r; log += l
        reward += self._handle_fainted(log)

        p_alive = self._alive(self.player_team)
        o_alive = self._alive(self.opponent_team)
        if not o_alive:
            reward += 10.0; terminated = True; log.append("🏆 플레이어 승리!")
        elif not p_alive:
            reward -= 10.0; terminated = True; log.append("💀 플레이어 패배...")
        elif self.turn >= self.max_turns:
            p_r = sum(p.hp_ratio for p in self.player_team)/self.team_size
            o_r = sum(p.hp_ratio for p in self.opponent_team)/self.team_size
            reward += (p_r - o_r) * 3.0; terminated = True; log.append("⏰ 턴 초과!")

        self.battle_log.extend(log)
        if self.render_mode == "ansi": print("\n".join(log))
        return self._get_obs(), reward, terminated, False, self._get_info()

    def _do_switch(self, side: str, new_idx: int):
        log = []
        reward = 0.0
        if side == "player":
            old = self.player_active
        else:
            old = self.opponent_active

        ab = self._get_ability(old)
        if ab.name == "regenerator":
            heal = old.max_hp // 3; old.heal(heal)
            log.append(f"  {old.name}의 재생력으로 HP {heal} 회복!")
        if ab.name == "natural-cure" and old.status != "none":
            old.status = "none"; log.append(f"  {old.name}의 자연회복으로 상태이상 회복!")

        if side == "player":
            self.player_active_idx = new_idx
            new = self.player_team[new_idx]
            log.append(f"플레이어: {old.name} → {new.name} 교체!")
            reward -= 0.05
        else:
            self.opponent_active_idx = new_idx
            new = self.opponent_team[new_idx]
            log.append(f"상대: {old.name} → {new.name} 교체!")

        reward += self._trigger_switch_in(new, log)
        return reward, log

    def _trigger_switch_in(self, pokemon, log):
        return self._get_ability(pokemon).on_switch_in(pokemon, self, log)

    def _execute_move(self, attacker, move, defender, label, is_player):
        log = [f"{label}의 {attacker.name}이(가) {move.name}을(를) 사용!"]
        reward = 0.0
        terminated = False

        can_move, slog = self._check_status_block(attacker)
        log += slog
        if not can_move: return reward, terminated, log

        if move.pp <= 0:
            log.append("PP가 없어 발버둥을 쳤다!")
            attacker.take_damage(max(1, attacker.current_hp // 4))
            return reward, terminated, log

        move.use()

        # 명중 체크
        from env.weather import get_weather_accuracy_modifier
        weather_acc = get_weather_accuracy_modifier(self.weather, move.name)
        if move.accuracy > 0 and weather_acc != float("inf"):
            if random.random() > move.accuracy / 100 * weather_acc:
                log.append("공격이 빗나갔다!")
                return reward, terminated, log

        if move.category == "status":
            reward += self._apply_status_move(attacker, defender, move, log)
            return reward, terminated, log

        # 데미지
        crit = random.random() < calc_critical_chance(move)
        type_mult = get_type_multiplier(move.type_, defender.types)

        atk_mult = self._get_ability(attacker).modify_damage(attacker, move, 0, True, self)
        def_mult = self._get_ability(defender).modify_damage(defender, move, 0, False, self)
        if def_mult == 0.0:
            log.append("효과가 없는 것 같다..."); return reward, terminated, log

        weather_mult = get_weather_move_modifier(self.weather, move.type_)
        terrain_mult = get_terrain(self.terrain).get_move_modifier(move.type_, attacker)

        base_dmg = calc_damage(attacker, defender, move, critical=crit)
        final_dmg = max(1, int(base_dmg * atk_mult * def_mult * weather_mult * terrain_mult))

        if type_mult == 0: log.append("효과가 없는 것 같다..."); return reward, terminated, log
        elif type_mult >= 2: log.append("효과가 굉장한 것 같다!")
        elif type_mult <= 0.5: log.append("효과가 별로인 것 같다...")
        if crit: log.append("급소에 맞았다!")

        prev_hp = defender.current_hp
        defender.take_damage(final_dmg)
        actual = prev_hp - defender.current_hp
        log.append(f"  → {defender.name}에게 {actual} 데미지! (HP: {defender.current_hp}/{defender.max_hp})")
        reward += actual / defender.max_hp * 2.0

        self._get_ability(defender).on_hit(defender, attacker, move, actual, self, log)
        reward += self._get_ability(attacker).on_attack(attacker, defender, move, actual, self, log)

        if move.effect and move.effect_chance > 0:
            if random.random() < move.effect_chance / 100:
                reward += self._apply_effect(attacker, defender, move.effect, log)

        if defender.is_fainted:
            log.append(f"{defender.name}은(는) 쓰러졌다!"); reward += 3.0

        return reward, terminated, log

    def _check_status_block(self, pokemon):
        log = []
        if pokemon.status == "sleep":
            pokemon.sleep_turns -= 1
            if pokemon.sleep_turns <= 0:
                pokemon.status = "none"; log.append(f"{pokemon.name}이(가) 잠에서 깨어났다!")
            else:
                log.append(f"{pokemon.name}은(는) 잠들어 있다..."); return False, log
        elif pokemon.status == "freeze":
            if random.random() < 0.2:
                pokemon.status = "none"; log.append(f"{pokemon.name}의 얼음이 녹았다!")
            else:
                log.append(f"{pokemon.name}은(는) 꽁꽁 얼어붙어 있다!"); return False, log
        elif pokemon.status == "paralysis":
            if random.random() < 0.25:
                log.append(f"{pokemon.name}은(는) 몸이 굳어 움직일 수 없다!"); return False, log
        return True, log

    def _apply_status_move(self, attacker, defender, move, log):
        reward = 0.0
        effect = move.effect
        target = defender if move.target == "opponent" else attacker
        terrain_obj = get_terrain(self.terrain)

        status_map = {"burn":"burn","poison":"poison","sleep":"sleep",
                      "paralyze":"paralysis","freeze":"freeze"}
        if effect in status_map:
            status = status_map[effect]
            if terrain_obj.prevents_status(status):
                log.append(f"지형 효과로 상태이상이 방지됐다!"); return reward
            if target.apply_status(status):
                log.append(f"{target.name}이(가) {effect} 상태가 됐다!"); reward += 0.5
            else:
                log.append(f"{target.name}에게는 효과가 없었다!")
        elif effect.startswith("boost_"):
            tokens = effect.split("_")
            try:
                delta = int(tokens[-1])
                stat_tokens = tokens[1:-1]
            except ValueError:
                delta = 1
                stat_tokens = tokens[1:]
            stat = "_".join(stat_tokens)
            stat_alias = {"sp_def":"sp_defense","sp_atk":"sp_attack","atk":"attack","def":"defense","spe":"speed"}
            stat = stat_alias.get(stat, stat)
            result = target.change_rank(stat, delta)
            log.append(f"{target.name}의 {stat}이(가) {'올라갔다!' if result=='up' else '더 오를 수 없다!'}")
            reward += 0.3 if result == "up" else 0.0
        elif effect == "heal_half":
            heal = target.max_hp // 2
            if self.weather == "sun":    heal = target.max_hp * 2 // 3
            elif self.weather != "none": heal = target.max_hp // 4
            target.heal(heal)
            log.append(f"{target.name}이(가) HP를 {heal} 회복했다!"); reward += 0.3
        elif effect.startswith("set_weather_"):
            new_w = effect.replace("set_weather_", "")
            self._change_weather(new_w, log)
        return reward

    def _apply_effect(self, attacker, defender, effect, log):
        reward = 0.0
        terrain_obj = get_terrain(self.terrain)
        status_map = {"burn":"burn","paralyze":"paralysis","freeze":"freeze",
                      "sleep":"sleep","poison":"poison"}
        if effect in status_map:
            status = status_map[effect]
            if not terrain_obj.prevents_status(status):
                if defender.apply_status(status):
                    log.append(f"{defender.name}이(가) {effect} 상태가 됐다!"); reward += 0.3
        elif effect == "flinch":
            defender.flinched = True
        elif effect.startswith("drop_"):
            parts = effect.split("_", 2)
            stat = "_".join(parts[1:])
            # 축약형 → 실제 속성명 변환
            stat_alias = {
                "sp_def": "sp_defense",
                "sp_atk": "sp_attack",
                "sp_spd": "sp_defense",
                "atk": "attack",
                "def": "defense",
                "spe": "speed",
            }
            stat = stat_alias.get(stat, stat)
            defender.change_rank(stat, -1)
            log.append(f"{defender.name}의 {stat}이(가) 떨어졌다!")
        return reward

    def _end_of_turn(self):
        log = []
        reward = 0.0
        for pokemon in [self.player_active, self.opponent_active]:
            if not pokemon.is_fainted:
                if self._get_ability(pokemon).name != "magic-guard":
                    apply_weather_end_of_turn(pokemon, self.weather, log)
                    self._apply_status_eot(pokemon, log)
                terrain_obj = get_terrain(self.terrain)
                terrain_obj.end_of_turn_effect(pokemon, log)
                r = self._get_ability(pokemon).end_of_turn(pokemon, self, log)
                if pokemon in self.player_team: reward += r

        if self.weather_turns > 0:
            self.weather_turns -= 1
            if self.weather_turns == 0:
                log.append(WEATHER_END_MSG.get(self.weather, "날씨가 돌아왔다."))
                self.weather = "none"
        if self.terrain_turns > 0:
            self.terrain_turns -= 1
            if self.terrain_turns == 0:
                log.append("지형 효과가 사라졌다."); self.terrain = "none"
        return reward, log

    def _apply_status_eot(self, pokemon, log):
        if pokemon.status == "burn":
            d = max(1, pokemon.max_hp // 16); pokemon.take_damage(d)
            log.append(f"  {pokemon.name}은(는) 화상으로 체력이 줄었다! (-{d}HP)")
        elif pokemon.status == "poison":
            d = max(1, pokemon.max_hp // 8); pokemon.take_damage(d)
            log.append(f"  {pokemon.name}은(는) 독으로 체력이 줄었다! (-{d}HP)")
        elif pokemon.status == "toxic":
            pokemon.toxic_counter += 1
            d = max(1, pokemon.max_hp * pokemon.toxic_counter // 16); pokemon.take_damage(d)
            log.append(f"  {pokemon.name}은(는) 맹독으로 체력이 줄었다! (-{d}HP)")

    def _handle_fainted(self, log):
        reward = 0.0
        if self.player_active.is_fainted:
            alive = self._alive(self.player_team)
            if alive:
                self.player_active_idx = alive[0]
                reward += self._trigger_switch_in(self.player_active, log)
                log.append(f"플레이어: {self.player_active.name} 등장!")
        if self.opponent_active.is_fainted:
            alive = self._alive(self.opponent_team)
            if alive:
                self.opponent_active_idx = alive[0]
                self._trigger_switch_in(self.opponent_active, log)
                log.append(f"상대: {self.opponent_active.name} 등장!")
        return reward

    def _change_weather(self, new_weather, log):
        if self.weather != new_weather:
            self.weather = new_weather
            self.weather_turns = 5
            log.append(WEATHER_START_MSG.get(new_weather, f"{new_weather}!"))

    def _parse_action(self, action, team, active_idx):
        if action < 4: return action, None
        switch_options = [i for i in range(self.team_size)
                          if i != active_idx and not team[i].is_fainted]
        slot = action - 4
        if slot < len(switch_options): return None, switch_options[slot]
        return random.randint(0, 3), None

    def _get_move(self, pokemon, idx):
        if not pokemon.moves: return Move("발버둥","normal","physical",50,100,1)
        return pokemon.moves[idx % len(pokemon.moves)]

    def _goes_first(self, p1, m1, p2, m2):
        if m1.priority != m2.priority: return m1.priority > m2.priority
        spd1, spd2 = p1.speed, p2.speed
        for p, spd in [(p1, spd1), (p2, spd2)]:
            ab = self._get_ability(p)
            if hasattr(ab, "get_speed_modifier"):
                mult = ab.get_speed_modifier(p, self)
                if p is p1: spd1 = int(spd1 * mult)
                else: spd2 = int(spd2 * mult)
        if spd1 != spd2: return spd1 > spd2
        return random.random() < 0.5

    def _opponent_policy(self):
        me, foe = self.opponent_active, self.player_active
        best_action, best_score = 0, -999
        for i, move in enumerate(me.moves[:4]):
            if move.pp <= 0: continue
            if move.category == "status":
                score = 0.5
            else:
                type_mult = get_type_multiplier(move.type_, foe.types)
                stab = 1.5 if move.type_ in me.types else 1.0
                weather_mult = get_weather_move_modifier(self.weather, move.type_)
                score = move.power * type_mult * stab * weather_mult
            if score > best_score: best_score = score; best_action = i
        if me.hp_ratio < 0.3:
            alive = [i for i in range(self.team_size)
                     if i != self.opponent_active_idx and not self.opponent_team[i].is_fainted]
            if alive and random.random() < 0.5: return 4
        return best_action

    def render(self):
        if self.render_mode != "ansi": return
        WN = {"rain":"🌧️","sun":"☀️","sandstorm":"🌪️","hail":"🌨️","snow":"❄️","none":""}
        print(f"\n{'='*60}")
        print(f"  📍 턴 {self.turn}  날씨: {WN.get(self.weather,'')} {self.weather}  지형: {self.terrain}")
        for label, poke in [("상대", self.opponent_active), ("나의", self.player_active)]:
            bar = "■"*int(poke.hp_ratio*20)+"□"*(20-int(poke.hp_ratio*20))
            ab = getattr(poke, "ability_name", "")
            print(f"  [{label}] {poke.name:8s} [{'/'.join(poke.types):15s}] "
                  f"HP:{poke.current_hp:3d}/{poke.max_hp:3d} [{bar}] {poke.status} | {ab}")
        print(f"{'='*60}")
