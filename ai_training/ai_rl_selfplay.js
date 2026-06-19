// RL self-play (HP 차등 보상 + 승패 보너스, 양측 랜덤 배정으로 편향 상쇄)
sleep=function(){return Promise.resolve();};addLog=function(m,c){};
renderBattle=function(){};renderActions=function(){};playBgm=function(){};
showSwitchPrompt=function(m){if(battleState)battleState.phase='switch';};
switchBattleTab=function(){};clearTurnTimer=function(){};startTurnTimer=function(){};checkServer=function(){};hideAllScreens=function(){};
const fs=require('fs');const W=JSON.parse(fs.readFileSync('/home/claude/ppo_weights.json'));
function mlp(obs){const[D,H1,H2,OUT]=W.arch;function L(v,Wf,bf,inN,outN,act){const o=new Array(outN);for(let j=0;j<outN;j++){let s=bf[j];for(let i=0;i<inN;i++)s+=v[i]*Wf[i*outN+j];o[j]=act?(s>0?s:0):s;}return o;}const a1=L(obs,W.W1,W.b1,D,H1,true),a2=L(a1,W.W2,W.b2,H1,H2,true);return L(a2,W.W3,W.b3,H2,OUT,false);}
function sampleAct(me,opp){const z=mlp(buildPpoObs(me,opp));const valid=[];me.moves.forEach((mv,i)=>{if(i<4&&mv.pp>0)valid.push(i);});if(!valid.length)return{obs:buildPpoObs(me,opp),action:0};
  let mx=-1e9;valid.forEach(i=>{if(z[i]>mx)mx=z[i];});let sm=0;const e={};valid.forEach(i=>{e[i]=Math.exp((z[i]-mx)/1.1);sm+=e[i];});
  let r=Math.random(),c=0,a=valid[0];for(const i of valid){c+=e[i]/sm;if(r<=c){a=i;break;}}return{obs:buildPpoObs(me,opp),action:a};}
function rngPick(a){return a[Math.floor(Math.random()*a.length)];}
function teamHP(side){return battleState[side].party.reduce((s,p)=>s+Math.max(0,p.currentHp)/p.maxHp,0);}

async function game(traj){
  aiDifficulty='hard';
  const t1=[],t2=[];while(t1.length<3){const p=rngPick(POKE_DB);if(p.allMoves&&p.allMoves.filter(m=>m.power>0).length>=2&&!t1.includes(p.id))t1.push(p.id);}
  while(t2.length<3){const p=rngPick(POKE_DB);if(p.allMoves&&p.allMoves.filter(m=>m.power>0).length>=2&&!t2.includes(p.id))t2.push(p.id);}
  const mk=(ids)=>ids.map(id=>buildBattlePoke(buildOptimizedAiPoke(POKE_DB.find(p=>p.id===id))));
  battleState={player:{party:mk(t1),active:0,revealed:[true,false,false]},ai:{party:mk(t2),active:0,revealed:[true,false,false]},turn:1,phase:'action',weather:null,weatherTurns:0,field:{stealthRockPlayer:false,stealthRockAi:false,spikesPlayer:0,spikesAi:0,toxicSpikesPlayer:0,toxicSpikesAi:0,stickyWebPlayer:false,stickyWebAi:false,reflectPlayer:0,reflectAi:0,lightScreenPlayer:0,lightScreenAi:0,auroraVeilPlayer:0,auroraVeilAi:0,trickRoom:0,tailwindPlayer:0,tailwindAi:0,terrain:null,terrainTurns:0},_pivotPending:false};
  let winner=null;showResult=function(w){winner=w?'player':'ai';battleState=null;};
  // ai측도 같은 NN 정책으로
  // ai측은 hard 휴리스틱 그대로 사용 (휴리스틱을 직접 상대로 학습)
  const steps=[];let g=0;
  while(battleState&&!battleState._ended&&g<200){g++;
    if(battleState.phase==='switch'){const a=battleState.player.party.findIndex(p=>p.currentHp>0);if(a===-1)break;battleState.player.active=a;battleState.phase='action';continue;}
    let act=battleState.player.party[battleState.player.active];if(!act||act.currentHp<=0){const a=battleState.player.party.findIndex(p=>p.currentHp>0);if(a===-1)break;battleState.player.active=a;act=battleState.player.party[a];}
    const pHP0=teamHP('player'),aHP0=teamHP('ai');
    const dec=sampleAct(act,battleState.ai.party[battleState.ai.active]);
    await playerMove(dec.action);
    // HP 차등 보상: 상대가 잃은 HP - 내가 잃은 HP
    const pHP1=battleState?teamHP('player'):pHP0, aHP1=battleState?teamHP('ai'):aHP0;
    const shaped=((aHP0-aHP1)-(pHP0-pHP1)); // +면 이득
    steps.push({obs:dec.obs,action:dec.action,shaped});
  }
  const winR=winner==='player'?1:(winner==='ai'?-1:0);
  steps.forEach(s=>{ const reward = s.shaped*3 + winR*0.5; traj.push({o:s.obs.map(x=>Math.round(x*1000)/1000),a:s.action,r:reward}); });
  return winner;
}
(async()=>{const N=parseInt(process.argv[2]||'1500');const traj=[];let pw=0,aw=0,err=0;
  for(let i=0;i<N;i++){try{const w=await game(traj);if(w==='player')pw++;else if(w==='ai')aw++;}catch(e){err++;}}
  fs.writeFileSync('/home/claude/rl_traj.json',JSON.stringify(traj));
  console.error(`selfplay ${N} | steps ${traj.length} | P${pw}/A${aw} err${err}`);})();
