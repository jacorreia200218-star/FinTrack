# Instalar a FinTrack num servidor grátis (a partir do iPad)

**Resultado:** a FinTrack fica num servidor gratuito da Oracle, sempre ligada, e abres-a no
Safari do iPad (ou do telemóvel) num endereço `https://fintrack.….ts.net`.

**Segurança:**
- A app **não fica visível na internet**. Só os aparelhos ligados à tua conta Tailscale lhe
  conseguem chegar.
- Para entrar na Tailscale usas a tua conta Apple ou Google, que já tem dois fatores.
- A própria app pede uma palavra-passe.
- Os dados ficam só no servidor. Nunca vão para o GitHub.

**Tempo:** cerca de 30 minutos. **Custo:** 0 €. A Oracle pede um cartão no registo, só para
verificar a identidade.

---

## Parte 1 — Tailscale (10 min)

1. No iPad, instala a app **Tailscale** da App Store, abre-a e entra com a tua conta Apple ou
   Google. Aceita adicionar a configuração de VPN.
2. No Safari, abre **login.tailscale.com/admin** e entra com a mesma conta.
3. Ativa o HTTPS:
   - vai a **DNS**;
   - confirma que o **MagicDNS** está ligado;
   - mais abaixo, em **HTTPS Certificates**, carrega em **Enable HTTPS**.
4. Cria a chave para o servidor:
   - vai a **Settings → Keys → Generate auth key…**;
   - deixa *Reusable* **desligado**;
   - carrega em **Generate key**;
   - **copia a chave** (começa por `tskey-auth-`) e guarda-a numa nota por uns minutos.
     Só é mostrada uma vez.

## Parte 2 — Oracle Cloud (20 min)

1. Abre **oracle.com/cloud/free** e carrega em **Start for free**.
   - **Home Region:** escolhe uma perto, por exemplo *Spain Central (Madrid)* ou
     *Germany Central (Frankfurt)*. Não dá para mudar depois.
   - Mete o cartão para verificação. Não é cobrado nada.
2. Já dentro da consola, abre o menu ☰ → **Compute → Instances → Create instance**.
3. Preenche:
   - **Name:** `fintrack`
   - **Image and shape → Edit:**
     - Image: **Change image → Ubuntu → Canonical Ubuntu 24.04**;
     - Shape: **Change shape → Specialty and previous generation → VM.Standard.E2.1.Micro**
       (tem a etiqueta *Always Free-eligible*).
   - **Networking:** deixa como está (cria uma rede nova com IP público).
   - **Add SSH keys:** escolhe **No SSH keys**. Se precisares de entrar no servidor, entras
     pela Tailscale.
4. Desce até **Show advanced options → Management → Initialization script** → **Paste
   cloud-init script** e cola isto, trocando `COLA-AQUI-A-CHAVE` pela chave da Tailscale:

   ```bash
   #!/bin/bash
   export TS_AUTHKEY="COLA-AQUI-A-CHAVE"
   export BRANCH="claude/trusting-babbage-fvv5kq"
   curl -fsSL "https://raw.githubusercontent.com/jacorreia200218-star/FinTrack/refs/heads/$BRANCH/deploy/install.sh" | bash
   ```

5. Carrega em **Create**. O servidor arranca e instala tudo sozinho, em 5 a 10 minutos.

> Se aparecer *"Out of capacity"*, tenta outra vez mais tarde. Também podes experimentar o
> shape **VM.Standard.A1.Flex** com 1 OCPU e 6 GB, que também é Always Free.

## Parte 3 — Abrir a app

1. Em **login.tailscale.com/admin → Machines** deve aparecer a máquina **fintrack**.
   - Carrega nos **⋯** dessa máquina → **Disable key expiry**.
   - Sem isto, o servidor sai da tua rede ao fim de 6 meses.
2. Copia o endereço da máquina (algo como `fintrack.tail1234.ts.net`).
3. Com a Tailscale ligada no iPad, abre no Safari **https://fintrack.tail1234.ts.net**.
4. Na primeira vez, a app pede para **criares a palavra-passe**: mínimo 10 caracteres. Deixa o
   iPad guardá-la nas Passwords.
5. Para ter a app como ícone: no Safari, **Partilhar → Adicionar ao ecrã principal**.

Pronto 🎉 Vai a **Importar** e carrega os teus extratos.

---

## Como funciona depois

- **Atualizações:** todas as noites (por volta das 4h) o servidor vai buscar a versão mais
  recente ao GitHub. Se a versão nova não arrancar, volta sozinho à anterior.
- **Cópias de segurança:** todas as noites guarda uma cópia da base de dados no próprio servidor
  (últimos 30 dias). De vez em quando, em **Importar → Descarregar cópia de segurança**, guarda
  também uma cópia no iCloud.
- **Servidor parado:** a Oracle pode desligar servidores grátis que passem uma semana quase sem
  atividade. Avisa por email antes. Os dados não se perdem: vais à consola da Oracle →
  **Instances → fintrack → Start**. Para evitar isto, podes passar a conta a *Pay As You Go*.
  Continua grátis enquanto ficares nos recursos Always Free, mas aí o cartão pode ser cobrado
  se criares outras coisas pagas.

## Se algo correr mal

- **A máquina não aparece na Tailscale ao fim de 15 minutos:** a chave pode ter expirado ou
  estar mal copiada. Apaga a instância na Oracle e repete a Parte 2 com uma chave nova.
- **O endereço não abre:** confirma que a Tailscale está ligada no iPad e que ativaste o
  *HTTPS Certificates* (Parte 1, passo 3). O servidor continua a tentar durante 2 horas
  depois de arrancar.
- **Esqueceste a palavra-passe:** entra no servidor pela Tailscale (app *Termius*, ligação a
  `fintrack`), corre `sudo rm /var/lib/fintrack/data/auth.json` e depois
  `sudo systemctl restart fintrack`. Os dados ficam; só voltas a criar a palavra-passe.
- **Registo da instalação:** está em `/var/log/fintrack-install.log` no servidor.
