# Pokemon Battle AI - Oracle Cloud 서버 설정 가이드

## 1단계: Oracle Cloud 계정 생성 (무료)

1. https://cloud.oracle.com 접속
2. "Start for free" 클릭
3. 계정 생성 (신용카드 필요하지만 과금되지 않음)
4. Always Free 리소스만 사용하면 영구 무료

## 2단계: VM 인스턴스 생성

1. Oracle Cloud Console 로그인
2. 좌측 메뉴 → Compute → Instances → Create Instance

설정:
- **Name**: pokemon-battle-server
- **Image**: Ubuntu 22.04 (기본값)
- **Shape**: VM.Standard.A1.Flex (ARM) → 1 OCPU, 6GB RAM (무료)
  - 또는 VM.Standard.E2.1.Micro (AMD, 1GB RAM, 무료)
- **Networking**: Create new VCN → 기본값
- **Add SSH keys**: Generate key pair → 다운로드 (중요!)

"Create" 클릭 → 2~3분 대기

## 3단계: 방화벽 설정 (포트 8765 개방)

### Oracle Cloud 보안 목록:
1. Networking → Virtual Cloud Networks → VCN 클릭
2. Subnets → 서브넷 클릭
3. Security Lists → Default Security List
4. Add Ingress Rules:
   - Source CIDR: 0.0.0.0/0
   - Protocol: TCP
   - Destination Port: 8765

### VM 내부 방화벽:
```bash
sudo iptables -I INPUT -p tcp --dport 8765 -j ACCEPT
sudo netfilter-persistent save
```

## 4단계: 서버 설정

SSH 접속:
```bash
ssh -i <다운받은키>.key ubuntu@<Public IP>
```

패키지 설치:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip -y
pip3 install fastapi uvicorn[standard] websockets pydantic
```

## 5단계: 파일 업로드

로컬 PC에서 서버로 파일 전송:
```bash
scp -i <키>.key server_deploy.py ubuntu@<IP>:~/
scp -i <키>.key pokemon_battle_v3.html ubuntu@<IP>:~/
scp -i <키>.key *.mp3 ubuntu@<IP>:~/
```

## 6단계: 서버 실행

### 테스트 실행:
```bash
python3 server_deploy.py
```
브라우저에서 http://<Public IP>:8765 접속하여 확인

### 자동 시작 설정 (systemd):
```bash
sudo tee /etc/systemd/system/pokemon-battle.service << EOF
[Unit]
Description=Pokemon Battle AI Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu
ExecStart=/usr/bin/python3 /home/ubuntu/server_deploy.py
Restart=always
RestartSec=5
Environment=PORT=8765

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable pokemon-battle
sudo systemctl start pokemon-battle
```

### 상태 확인:
```bash
sudo systemctl status pokemon-battle
# 로그 보기
sudo journalctl -u pokemon-battle -f
```

## 7단계: 클라이언트 설정

pokemon_battle_v3.html 에서 SERVER 변수를 수정:
```javascript
let SERVER = 'http://<Oracle Cloud Public IP>:8765';
```

또는 run_server.py / app.py 에서 서버 주소를 변경

## 서버 관리

### 서버 재시작:
```bash
sudo systemctl restart pokemon-battle
```

### 서버 중지:
```bash
sudo systemctl stop pokemon-battle
```

### 로그 확인:
```bash
sudo journalctl -u pokemon-battle --since "1 hour ago"
```

## 완성 후 구조

```
[Oracle Cloud 서버] (항상 켜져있음, 영구 무료)
├── server_deploy.py (FastAPI + WebSocket)
├── pokemon_battle_v3.html (게임 HTML)
└── *.mp3 (BGM 파일)

[유저 PC]
└── PokemonBattle.exe 실행
    → 브라우저 열림
    → Oracle Cloud 서버에 자동 접속
    → 싱글플레이 (로컬 AI)
    → 멀티플레이 (서버 중계)
```
