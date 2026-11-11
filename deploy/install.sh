#!/bin/bash
# Instala a FinTrack num servidor Ubuntu (pensado para a Oracle Cloud "Always Free").
#
# Corre como root. Pode ser colado como "script de inicialização" (cloud-init) ao criar o
# servidor, ou corrido à mão. Pode correr várias vezes sem estragar nada.
#
# Variáveis (opcionais):
#   TS_AUTHKEY  chave da Tailscale (tskey-auth-...) para o servidor entrar na tua rede sozinho
#   BRANCH      ramo do GitHub a usar (por omissão: main)
#   REPO_URL    repositório (por omissão: o da FinTrack)
#
# O que faz:
#   - instala Python e cria um utilizador de sistema "fintrack" sem login
#   - descarrega a app para /opt/fintrack; dados em /var/lib/fintrack (fora do código)
#   - serviço systemd que só escuta em 127.0.0.1 (nada fica aberto à internet)
#   - Tailscale: a app fica acessível só nos teus aparelhos, com HTTPS
#   - atualização automática diária a partir do GitHub (com volta atrás se falhar)
#   - cópia de segurança diária da base de dados (guarda 30 dias)

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/jacorreia200218-star/FinTrack.git}"
BRANCH="${BRANCH:-main}"
APP_DIR=/opt/fintrack
DATA_DIR=/var/lib/fintrack
LOG=/var/log/fintrack-install.log

exec > >(tee -a "$LOG") 2>&1
echo "=== FinTrack: instalação $(date -Is) (ramo $BRANCH) ==="

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-venv sqlite3 curl ca-certificates

# O servidor grátis tem 1 GB de RAM: um ficheiro de swap evita falhas a instalar pacotes
if [ -z "$(swapon --noheadings)" ]; then
  [ -f /swapfile ] || { fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile; }
  if swapon /swapfile; then
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  else
    echo "aviso: não consegui ativar o swap (não é grave)"
  fi
fi

id fintrack >/dev/null 2>&1 || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin fintrack
install -d -o fintrack -g fintrack -m 700 "$DATA_DIR" "$DATA_DIR/data" "$DATA_DIR/backups"

# ---------- código ----------
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch origin "$BRANCH"
  git -C "$APP_DIR" reset --hard "origin/$BRANCH"
fi
echo "$BRANCH" > "$APP_DIR/.deploy-branch"
git config --system --add safe.directory "$APP_DIR"
chown -R root:root "$APP_DIR"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

# ---------- serviço ----------
cat > /etc/systemd/system/fintrack.service <<EOF
[Unit]
Description=FinTrack
After=network-online.target
Wants=network-online.target

[Service]
User=fintrack
Group=fintrack
WorkingDirectory=$APP_DIR
Environment=FINTRACK_DB=$DATA_DIR/data/fintrack.db
Environment=FINTRACK_REQUIRE_LOGIN=1
Environment=FINTRACK_HOST=127.0.0.1
Environment=FINTRACK_PORT=8000
ExecStart=$APP_DIR/.venv/bin/python -m fintrack
Restart=always
RestartSec=3
# Isolamento: o serviço só pode escrever na pasta de dados
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DATA_DIR
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

# ---------- atualização + cópia de segurança diárias ----------
install -m 755 "$APP_DIR/deploy/fintrack-update" /usr/local/bin/fintrack-update

cat > /etc/systemd/system/fintrack-update.service <<'EOF'
[Unit]
Description=FinTrack: cópia de segurança e atualização
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/fintrack-update
EOF

cat > /etc/systemd/system/fintrack-update.timer <<'EOF'
[Unit]
Description=FinTrack: cópia de segurança e atualização diárias

[Timer]
OnCalendar=*-*-* 04:00:00
RandomizedDelaySec=30m
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now fintrack.service fintrack-update.timer
systemctl restart fintrack.service

# ---------- Tailscale ----------
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled

if [ -n "${TS_AUTHKEY:-}" ]; then
  # --ssh: permite entrar no servidor a partir dos teus aparelhos (sem abrir a porta 22)
  tailscale up --auth-key="$TS_AUTHKEY" --hostname=fintrack --ssh
fi

if tailscale status >/dev/null 2>&1; then
  # HTTPS dentro da tua rede Tailscale -> app local. Se o HTTPS ainda não estiver ativo na
  # consola da Tailscale, continua a tentar em segundo plano durante 2 horas.
  if ! tailscale serve --bg 8000; then
    # shellcheck disable=SC2016  # o $(seq) é expandido dentro do bash -c
    nohup bash -c 'for i in $(seq 240); do tailscale serve --bg 8000 && exit 0; sleep 30; done' \
      >>"$LOG" 2>&1 &
  fi
  echo
  echo ">>> Pronto. No iPad (com a Tailscale ligada) abre: https://$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
else
  echo
  echo ">>> Falta ligar à Tailscale: corre 'sudo tailscale up --hostname=fintrack --ssh' e depois 'sudo tailscale serve --bg 8000'."
fi
echo "=== FinTrack: instalação terminada $(date -Is) ==="
