"""
export_ppo_to_js.py - PPO 모델을 JavaScript로 변환
학습된 신경망을 HTML 배틀 AI에 탑재

사용법:
  python export_ppo_to_js.py --model final_model.pt --output ai_weights.js
  
  생성된 ai_weights.js를 pokemon_battle_v3.html에 삽입하면
  학습된 PPO AI가 브라우저에서 직접 실행됩니다.
"""
import argparse
import json
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description='PPO 모델 → JavaScript 변환')
    parser.add_argument('--model', default='final_model.pt', help='모델 파일 경로')
    parser.add_argument('--output', default='ai_weights.js', help='출력 JS 파일')
    parser.add_argument('--html', default=None, help='HTML에 직접 삽입 (선택)')
    args = parser.parse_args()
    
    # PyTorch 로드
    try:
        import torch
        import numpy as np
    except ImportError:
        print("[ERROR] PyTorch가 필요합니다: pip install torch numpy")
        return
    
    model_path = Path(args.model)
    if not model_path.exists():
        print(f"[ERROR] 모델 파일을 찾을 수 없습니다: {model_path}")
        return
    
    print(f"[1/4] 모델 로드: {model_path}")
    
    # 모델 로드
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from agents.ppo_agent import PPOAgent
        
        agent = PPOAgent(obs_dim=84, n_actions=6, hidden_dim=256)
        agent.load(str(model_path))
        
        # Actor 네트워크 가중치 추출
        actor = agent.actor
        print(f"[2/4] Actor 네트워크 구조 분석...")
        
        layers = []
        for name, param in actor.named_parameters():
            w = param.detach().cpu().numpy()
            print(f"  {name}: {w.shape}")
            layers.append({
                'name': name,
                'shape': list(w.shape),
                'data': w.flatten().tolist()
            })
        
    except Exception as e:
        print(f"[ERROR] 모델 로드 실패: {e}")
        print("\n대안: state_dict에서 직접 추출...")
        
        try:
            checkpoint = torch.load(str(model_path), map_location='cpu')
            if isinstance(checkpoint, dict):
                if 'actor_state_dict' in checkpoint:
                    state_dict = checkpoint['actor_state_dict']
                elif 'model_state_dict' in checkpoint:
                    state_dict = checkpoint['model_state_dict']
                elif 'state_dict' in checkpoint:
                    state_dict = checkpoint['state_dict']
                else:
                    # Try to find actor keys
                    actor_keys = [k for k in checkpoint.keys() if 'actor' in k.lower() or 'policy' in k.lower()]
                    if actor_keys:
                        state_dict = {k: checkpoint[k] for k in actor_keys}
                    else:
                        state_dict = checkpoint
            else:
                state_dict = checkpoint.state_dict() if hasattr(checkpoint, 'state_dict') else {}
            
            layers = []
            for name, param in state_dict.items():
                if 'actor' in name or 'policy' in name or 'fc' in name or 'linear' in name:
                    w = param.cpu().numpy() if isinstance(param, torch.Tensor) else np.array(param)
                    print(f"  {name}: {w.shape}")
                    layers.append({
                        'name': name,
                        'shape': list(w.shape),
                        'data': w.flatten().tolist()
                    })
            
            if not layers:
                print("[WARN] Actor 레이어를 찾을 수 없어 모든 레이어를 내보냅니다.")
                for name, param in state_dict.items():
                    w = param.cpu().numpy() if isinstance(param, torch.Tensor) else np.array(param)
                    print(f"  {name}: {w.shape}")
                    layers.append({
                        'name': name,
                        'shape': list(w.shape),
                        'data': w.flatten().tolist()
                    })
                    
        except Exception as e2:
            print(f"[ERROR] 가중치 추출 실패: {e2}")
            return
    
    if not layers:
        print("[ERROR] 추출된 레이어가 없습니다.")
        return
    
    print(f"[3/4] JavaScript 생성 중... ({len(layers)}개 레이어)")
    
    # JavaScript 코드 생성
    js_code = """
// ===== PPO AI WEIGHTS (auto-generated) =====
// Model: {model_name}
// Layers: {n_layers}
const PPO_WEIGHTS = {weights_json};

// ===== PPO Neural Network (JavaScript) =====
function ppoForward(obs, mask) {{
  // obs: 84-dim input, mask: 6-dim action mask
  const w = PPO_WEIGHTS;
  
  // Find weight/bias pairs
  const weightLayers = w.filter(l => l.name.includes('weight'));
  const biasLayers = w.filter(l => l.name.includes('bias'));
  
  let x = obs.slice(); // copy
  
  for (let i = 0; i < weightLayers.length; i++) {{
    const wl = weightLayers[i];
    const bl = biasLayers[i] || null;
    const [outDim, inDim] = wl.shape;
    
    // Matrix multiply: out = W * x + b
    const out = new Array(outDim).fill(0);
    for (let o = 0; o < outDim; o++) {{
      let sum = bl ? bl.data[o] : 0;
      for (let j = 0; j < Math.min(inDim, x.length); j++) {{
        sum += wl.data[o * inDim + j] * x[j];
      }}
      out[o] = sum;
    }}
    
    // ReLU activation (except last layer)
    if (i < weightLayers.length - 1) {{
      for (let o = 0; o < out.length; o++) {{
        out[o] = Math.max(0, out[o]);
      }}
    }}
    
    x = out;
  }}
  
  // Apply action mask (masked actions get -infinity)
  if (mask) {{
    for (let i = 0; i < Math.min(x.length, mask.length); i++) {{
      if (mask[i]) x[i] = -1e9;
    }}
  }}
  
  // Softmax → action probabilities
  const maxVal = Math.max(...x);
  const exp = x.map(v => Math.exp(v - maxVal));
  const sum = exp.reduce((a,b) => a+b, 0);
  const probs = exp.map(v => v / sum);
  
  // Sample action (or argmax for deterministic)
  let bestIdx = 0, bestProb = -1;
  for (let i = 0; i < probs.length; i++) {{
    if (probs[i] > bestProb) {{ bestProb = probs[i]; bestIdx = i; }}
  }}
  
  return {{ action: bestIdx, probs: probs }};
}}

// PPO AI가 사용 가능한지 확인
const PPO_AI_AVAILABLE = PPO_WEIGHTS && PPO_WEIGHTS.length > 0;
console.log('[PPO AI] ' + (PPO_AI_AVAILABLE ? 'Loaded (' + PPO_WEIGHTS.length + ' layers)' : 'Not available'));
""".format(
        model_name=model_path.name,
        n_layers=len(layers),
        weights_json=json.dumps(layers, separators=(',', ':'))
    )
    
    # 파일 크기 계산
    js_size = len(js_code)
    print(f"  JS 파일 크기: {js_size / 1024:.0f} KB")
    
    # JS 파일 저장
    output_path = Path(args.output)
    output_path.write_text(js_code, encoding='utf-8')
    print(f"[4/4] 저장 완료: {output_path}")
    
    # HTML에 삽입
    if args.html:
        html_path = Path(args.html)
        if html_path.exists():
            html = html_path.read_text(encoding='utf-8')
            
            # 기존 PPO_WEIGHTS가 있으면 교체
            if 'PPO_WEIGHTS' in html:
                import re
                html = re.sub(
                    r'// ===== PPO AI WEIGHTS.*?console\.log\(\'\[PPO AI\].*?\n',
                    js_code,
                    html,
                    flags=re.DOTALL
                )
                print(f"  HTML 업데이트: {html_path} (기존 가중치 교체)")
            else:
                # </script> 앞에 삽입
                insert_point = html.rfind('</script>')
                if insert_point > 0:
                    html = html[:insert_point] + '\n' + js_code + '\n' + html[insert_point:]
                    print(f"  HTML 삽입: {html_path}")
                    
            html_path.write_text(html, encoding='utf-8')
        else:
            print(f"  [WARN] HTML 파일 없음: {html_path}")
    
    print(f"\n사용 방법:")
    print(f"  1. {output_path}를 pokemon_battle_v3.html의 <script> 안에 복사")
    print(f"  2. 또는: python export_ppo_to_js.py --model {model_path} --html pokemon_battle_v3.html")
    print(f"\n  HTML에서 ppoForward(obs, mask)로 호출 가능")


if __name__ == "__main__":
    main()
