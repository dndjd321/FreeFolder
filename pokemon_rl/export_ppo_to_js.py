"""
export_ppo_to_js.py - PPO 모델을 JavaScript로 변환
사용법: python export_ppo_to_js.py --model checkpoints/final_model.pt --html pokemon_battle_v3.html
"""
import argparse
import json
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='final_model.pt')
    parser.add_argument('--html', default=None)
    parser.add_argument('--output', default='ai_weights.js')
    args = parser.parse_args()

    try:
        import torch
        import numpy as np
    except ImportError:
        print("[ERROR] pip install torch numpy")
        return

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"[ERROR] 파일 없음: {model_path}")
        return

    print(f"[1/4] 모델 로드: {model_path}")
    checkpoint = torch.load(str(model_path), map_location='cpu')

    # 모델 구조 파악
    if isinstance(checkpoint, dict):
        print(f"  키: {list(checkpoint.keys())}")
    
    # network_state에서 가중치 찾기
    state_dict = None
    for key in ['network_state', 'actor_state_dict', 'model_state_dict', 'state_dict']:
        if isinstance(checkpoint, dict) and key in checkpoint:
            state_dict = checkpoint[key]
            print(f"  → '{key}'에서 가중치 발견")
            break
    
    if state_dict is None and isinstance(checkpoint, dict):
        # 직접 weight/bias 키가 있는지 확인
        has_weights = any('weight' in k or 'bias' in k for k in checkpoint.keys())
        if has_weights:
            state_dict = checkpoint
            print(f"  → 최상위에서 가중치 발견")
        else:
            # 모든 하위 dict 탐색
            for k, v in checkpoint.items():
                if isinstance(v, dict):
                    has_w = any('weight' in sk or 'bias' in sk for sk in v.keys())
                    if has_w:
                        state_dict = v
                        print(f"  → '{k}'에서 가중치 발견")
                        break

    if state_dict is None:
        print("[ERROR] 가중치를 찾을 수 없습니다.")
        print("  모델 구조:")
        def print_structure(d, indent=2):
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, torch.Tensor):
                        print(f"{' '*indent}{k}: Tensor {list(v.shape)}")
                    elif isinstance(v, dict):
                        print(f"{' '*indent}{k}: dict ({len(v)} keys)")
                        if indent < 6:
                            print_structure(v, indent+2)
                    else:
                        print(f"{' '*indent}{k}: {type(v).__name__}")
        print_structure(checkpoint)
        return

    print(f"\n[2/4] 가중치 분석...")
    
    # Actor/Policy 관련 레이어만 추출 (없으면 전부)
    actor_keys = [k for k in state_dict.keys() 
                  if isinstance(state_dict[k], torch.Tensor) and 
                  ('weight' in k or 'bias' in k)]
    
    # actor/policy 키가 있으면 그것만, 없으면 전부
    policy_keys = [k for k in actor_keys if 'actor' in k or 'policy' in k or 'pi' in k]
    if policy_keys:
        actor_keys = policy_keys
        print(f"  Actor 전용 레이어 발견: {len(actor_keys)}개")
    else:
        print(f"  전체 레이어 사용: {len(actor_keys)}개")

    if not actor_keys:
        print("[ERROR] weight/bias 레이어가 없습니다.")
        return

    layers = []
    for name in actor_keys:
        tensor = state_dict[name]
        if isinstance(tensor, torch.Tensor):
            w = tensor.detach().cpu().numpy()
        else:
            w = np.array(tensor)
        print(f"  {name}: {list(w.shape)}")
        layers.append({
            'name': name,
            'shape': list(w.shape),
            'data': [round(float(x), 6) for x in w.flatten().tolist()]
        })

    print(f"\n[3/4] JavaScript 생성... ({len(layers)}개 레이어)")

    js_code = f"""
// ===== PPO AI WEIGHTS (auto-generated) =====
// Model: {model_path.name}
// Layers: {len(layers)}
const PPO_WEIGHTS = {json.dumps(layers, separators=(',', ':'))};

// ===== PPO Neural Network (JavaScript) =====
function ppoForward(obs, mask) {{
  const w = PPO_WEIGHTS;
  const weightLayers = w.filter(l => l.name.includes('weight'));
  const biasLayers = w.filter(l => l.name.includes('bias'));
  
  let x = obs.slice();
  
  for (let i = 0; i < weightLayers.length; i++) {{
    const wl = weightLayers[i];
    const bl = biasLayers[i] || null;
    const [outDim, inDim] = wl.shape;
    
    const out = new Array(outDim).fill(0);
    for (let o = 0; o < outDim; o++) {{
      let sum = bl ? bl.data[o] : 0;
      for (let j = 0; j < Math.min(inDim, x.length); j++) {{
        sum += wl.data[o * inDim + j] * x[j];
      }}
      out[o] = sum;
    }}
    
    // ReLU (except last layer)
    if (i < weightLayers.length - 1) {{
      for (let o = 0; o < out.length; o++) {{
        out[o] = Math.max(0, out[o]);
      }}
    }}
    x = out;
  }}
  
  // Action mask
  if (mask) {{
    for (let i = 0; i < Math.min(x.length, mask.length); i++) {{
      if (mask[i]) x[i] = -1e9;
    }}
  }}
  
  // Softmax
  const maxVal = Math.max(...x);
  const exp = x.map(v => Math.exp(v - maxVal));
  const sum = exp.reduce((a,b) => a+b, 0);
  const probs = exp.map(v => v / sum);
  
  let bestIdx = 0, bestProb = -1;
  for (let i = 0; i < probs.length; i++) {{
    if (probs[i] > bestProb) {{ bestProb = probs[i]; bestIdx = i; }}
  }}
  
  return {{ action: bestIdx, probs: probs }};
}}

const PPO_AI_AVAILABLE = PPO_WEIGHTS && PPO_WEIGHTS.length > 0;
console.log('[PPO AI] ' + (PPO_AI_AVAILABLE ? 'Loaded (' + PPO_WEIGHTS.length + ' layers)' : 'Not available'));
"""

    js_size = len(js_code)
    print(f"  JS 크기: {js_size / 1024:.0f} KB")

    # JS 파일 저장
    output_path = Path(args.output)
    output_path.write_text(js_code, encoding='utf-8')
    print(f"  저장: {output_path}")

    # HTML에 삽입
    if args.html:
        html_path = Path(args.html)
        if html_path.exists():
            html = html_path.read_text(encoding='utf-8')

            # 기존 PPO_WEIGHTS 제거
            import re
            html = re.sub(
                r'\n// ===== PPO AI WEIGHTS \(auto-generated\).*?console\.log\(\'\[PPO AI\].*?\'\);?\n',
                '\n',
                html,
                flags=re.DOTALL
            )

            # </script> 마지막 앞에 삽입
            insert_point = html.rfind('</script>')
            if insert_point > 0:
                html = html[:insert_point] + '\n' + js_code + '\n' + html[insert_point:]
                html_path.write_text(html, encoding='utf-8')
                print(f"  HTML 삽입 완료: {html_path}")
            else:
                print(f"  [WARN] </script> 태그를 찾을 수 없음")
        else:
            print(f"  [WARN] HTML 파일 없음: {html_path}")

    print(f"\n[4/4] 완료!")
    print(f"  규칙 AI + PPO 신경망이 HTML에 탑재되었습니다.")

if __name__ == "__main__":
    main()
