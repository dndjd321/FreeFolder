// ===== 자가대전 데이터 수집기 =====
// 양쪽 다 휴리스틱 AI로 싸우며 (관측값, 행동, 최종승패)를 기록
sleep=function(){return Promise.resolve();};addLog=function(m,c){};
renderBattle=function(){};renderActions=function(){};playBgm=function(){};
showSwitchPrompt=function(m){if(battleState)battleState.phase='switch';};
switchBattleTab=function(){};clearTurnTimer=function(){};startTurnTimer=function(){};
checkServer=function(){};hideAllScreens=function(){};
const fs=require('fs');

function rngPick(a){return a[Math.floor(Math.random()*a.length)];}
function team3(){const ids=[];while(ids.length<3){const p=rngPick(POKE_DB);if(!ids.includes(p.id)&&p.allMoves&&p.allMoves.filter(m=>m.power>0).length>=2)ids.push(p.id);}
  return ids.map(id=>buildBattlePoke(buildAiPoke(POKE_DB.find(p=>p.id===id),{hp:85,atk:85,def:85,spatk:85,spdef:85,spd:85},'mixed')));}

// 한 배틀 자가대전, 결정 기록 반환
function pickActionRecord(side){
  // side가 행동할 차례: 관측값 + 휴리스틱이 고른 행동 인덱스
  const me = side==='ai' ? getActive('ai') : getActive('player');
  const opp = side==='ai' ? getActive('player') : getActive('ai');
  // 관측값은 (자기, 상대) 기준 — buildPpoObs는 (ai, player) 시그니처라 자기를 첫 인자로
  const obs = buildPpoObs(me, opp);
  const mv = aiPickMove(me, opp);
  let idx = me.moves.findIndex(m=>m.name===mv.name && m.pp>0);
  if (idx<0) idx = 0;
  return { obs, action: idx, side };
}

async function selfPlayBattle(records){
  aiDifficulty = 'expert'; // 최강 휴리스틱으로 데이터 생성
  const player=genDifficultyAiTeam(3).map(buildBattlePoke), ai=genDifficultyAiTeam(3).map(buildBattlePoke);
  battleState={player:{party:player,active:0,revealed:[true,false,false]},ai:{party:ai,active:0,revealed:[true,false,false]},turn:1,phase:'action',weather:null,weatherTurns:0,field:{stealthRockPlayer:false,stealthRockAi:false,spikesPlayer:0,spikesAi:0,toxicSpikesPlayer:0,toxicSpikesAi:0,stickyWebPlayer:false,stickyWebAi:false,reflectPlayer:0,reflectAi:0,lightScreenPlayer:0,lightScreenAi:0,auroraVeilPlayer:0,auroraVeilAi:0,trickRoom:0,tailwindPlayer:0,tailwindAi:0,terrain:null,terrainTurns:0},_pivotPending:false};
  let winner=null;
  showResult=function(won){winner=won?'player':'ai';battleState=null;};
  const battleRecords=[];
  let guard=0;
  while(battleState&&!battleState._ended&&guard<400){guard++;
    if(battleState.phase==='switch'){const a=battleState.player.party.findIndex(p=>p.currentHp>0);if(a===-1)break;battleState.player.active=a;battleState.phase='action';continue;}
    let act=battleState.player.party[battleState.player.active];
    if(!act||act.currentHp<=0){const a=battleState.player.party.findIndex(p=>p.currentHp>0);if(a===-1)break;battleState.player.active=a;act=battleState.player.party[a];}
    // 플레이어(자가대전이므로 AI가 플레이어 역할도) 행동 기록
    const rec = pickActionRecord('player');
    battleRecords.push(rec);
    await playerMove(rec.action);
  }
  // 승패 라벨링 (player 관점 기록이므로 player가 이기면 1)
  const label = winner==='player' ? 1 : 0;
  battleRecords.forEach(r=>{ records.push({obs:r.obs, action:r.action, win:label}); });
  return winner;
}

(async()=>{
  const N = parseInt(process.argv[2] || '3000');
  const records = [];
  let done=0, errors=0;
  const t0=Date.now();
  for(let i=0;i<N;i++){
    try{ await selfPlayBattle(records); done++; }
    catch(e){ errors++; }
    if((i+1)%500===0) console.error(`  ${i+1}/${N} battles | records: ${records.length} | errors: ${errors}`);
  }
  const secs=((Date.now()-t0)/1000).toFixed(1);
  console.error(`\n완료: ${done} battles, ${records.length} decisions, ${errors} errors, ${secs}s`);
  // 통계
  const wins = records.filter(r=>r.win===1).length;
  console.error(`승리 행동: ${wins} (${Math.round(wins/records.length*100)}%) / 패배 행동: ${records.length-wins}`);
  // 저장 (압축: obs는 소수3자리)
  const out = records.map(r=>({o:r.obs.map(x=>Math.round(x*1000)/1000), a:r.action, w:r.win}));
  fs.writeFileSync('/home/claude/selfplay_data.json', JSON.stringify(out));
  console.error(`저장: selfplay_data.json (${(JSON.stringify(out).length/1e6).toFixed(1)} MB)`);
})();
