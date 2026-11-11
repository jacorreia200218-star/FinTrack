# Instalar a FinTrack num servidor grátis (a partir do iPad)

**Resultado:** a FinTrack fica num servidor gratuito do **Google Cloud**, sempre ligada, e
abres-a no Safari do iPad ou do telemóvel num endereço `https://fintrack.….ts.net`.

**Segurança:**
- A app **não fica visível na internet**. Só os teus aparelhos com a Tailscale lhe chegam.
- Para entrar na Tailscale usas a tua conta Apple ou Google, que já tem dois fatores.
- A própria app pede uma palavra-passe.
- Os dados ficam só no servidor. Nunca vão para o GitHub.

**Tempo:** cerca de 30 minutos, de preferência no iPad.

**Custo:** 0 €, se seguires as regras marcadas com 💶. O Google pede um cartão, e há um alerta
que te avisa por email se aparecer 1 € de custo.

---

## Parte 1 — Tailscale (10 min)

1. Instala a app **Tailscale** da App Store, abre-a e entra com a tua conta Apple ou Google.
   Aceita adicionar a configuração de VPN.
2. No Safari, abre **login.tailscale.com/admin** e entra com a mesma conta.
3. Ativa o HTTPS:
   - vai a **DNS**;
   - confirma que o **MagicDNS** está ligado;
   - mais abaixo, em **HTTPS Certificates**, carrega em **Enable HTTPS**.
4. Cria a chave para o servidor:
   - vai a **Settings → Keys → Generate auth key…**;
   - deixa *Reusable* **desligado**;
   - carrega em **Generate key**;
   - **copia a chave** (começa por `tskey-auth-`) para uma nota. Só é mostrada uma vez.

> Durante a Parte 2, **desliga a Tailscale** no iPad. O registo no Google às vezes queixa-se de
> VPN.

## Parte 2 — Google Cloud (20 min)

### 2.1 Criar a conta

1. Abre **cloud.google.com/free** → **Get started for free** e entra com uma conta Google.
2. País: **Portugal**. Tipo de conta: **Individual**. Mete o cartão.
   - O Google dá 300 $ de crédito para 90 dias. Não vamos usar o crédito: o servidor que vamos
     criar é grátis para sempre.

### 2.2 💶 Alerta de custos (2 min, não saltes)

1. Menu ☰ → **Billing** (Faturação) → **Budgets & alerts** → **Create budget**.
2. Nome: `alerta`. Valor: **1 €**. Deixa os avisos a 50 %, 90 % e 100 %. Guarda.

A partir daqui, se algum dia houver um custo, recebes um email.

### 2.3 Criar o servidor

> Se a consola estiver em português, as secções chamam-se **Configuração da máquina**,
> **SO e armazenamento** (botão **Alterar**), **Proteção de dados**, **Rede**,
> **Observabilidade** e **Avançado → Automação → Script de inicialização**. O disco certo
> chama-se **Disco permanente padrão**. O "equilibrado", que vem por defeito, é pago.

1. Menu ☰ → **Compute Engine → VM instances**. Se pedir, carrega em **Enable** e espera um
   minuto.
2. Carrega em **Create instance** e preenche:

   | Campo | Valor |
   |---|---|
   | **Name** | `fintrack` |
   | **Region** 💶 | **us-central1 (Iowa)**. Também servem `us-west1` ou `us-east1`; mais nenhuma é grátis. |
   | **Zone** | qualquer uma |
   | **Machine configuration** 💶 | série **E2** → tipo **e2-micro** |

   O preço estimado à direita pode mostrar um valor por mês. É normal: as horas do e2-micro são
   descontadas na fatura.

3. Em **OS and storage → Change**:

   | Campo | Valor |
   |---|---|
   | Operating system | **Ubuntu** |
   | Version | **Ubuntu 24.04 LTS** (x86/64, **não** a "Minimal") |
   | **Boot disk type** 💶 | **Standard persistent disk**. O que vem escolhido por defeito é pago. |
   | Size | **30 GB** (o máximo grátis) |

   Carrega em **Select**.
4. Em **Data protection / Proteção de dados** 💶: escolhe **No backups / Sem backups**. Por
   defeito, o Google liga uma "programação de snapshot" (`default-schedule-1`), e os snapshots
   são pagos. A FinTrack já faz as suas próprias cópias.
5. Se aparecer **Observability → Install Ops Agent**: **desliga**. Gasta memória e não é
   preciso.
6. **Networking:** deixa como está. **Não** marques "Allow HTTP/HTTPS traffic".
7. **Advanced → Automation → Startup script:** cola isto, trocando `COLA-AQUI-A-CHAVE` pela
   chave da Tailscale:

   ```bash
   #!/bin/bash
   # Só instala na primeira vez que o servidor arranca
   [ -f /var/lib/fintrack/.installed ] && exit 0
   export TS_AUTHKEY="COLA-AQUI-A-CHAVE"
   export BRANCH="claude/trusting-babbage-fvv5kq"
   curl -fsSL "https://raw.githubusercontent.com/jacorreia200218-star/FinTrack/refs/heads/$BRANCH/deploy/install.sh" | bash
   ```

8. Carrega em **Create**. O servidor instala tudo sozinho em 5 a 10 minutos.

## Parte 3 — Abrir a app

1. Volta a **ligar a Tailscale** no iPad.
2. Em **login.tailscale.com/admin → Machines** deve aparecer **fintrack**.
   - Carrega nos **⋯** dessa máquina → **Disable key expiry**.
   - Sem isto, o servidor sai da tua rede ao fim de 6 meses.
3. Copia o endereço da máquina (algo como `fintrack.tail1234.ts.net`) e abre no Safari
   **https://fintrack.tail1234.ts.net**.
4. Na primeira vez, a app pede para **criares a palavra-passe** (mínimo 10 caracteres). Deixa o
   iPad guardá-la.
5. Para teres um ícone: no Safari, **Partilhar → Adicionar ao ecrã principal**.

No telemóvel: instala a Tailscale com a **mesma conta** e abre o mesmo endereço.

Pronto 🎉 Vai a **Importar** e carrega os teus extratos.

---

### 2.4 💶 Confirmar antes de continuar

Menu da esquerda → **Storage → Discos**. A linha `fintrack` tem de mostrar:
- **Tipo:** Disco permanente padrão / Standard persistent disk
- **Programação de snapshot:** vazia

Se não mostrar, apaga a instância (o disco vai junto), apaga também os snapshots em
**Storage → Snapshots**, e cria de novo. Se o servidor já tinha entrado na Tailscale, remove-o
lá e gera uma chave nova.

## 💶 Importante: antes de passarem 90 dias

O período de experiência do Google acaba 90 dias depois do registo. **Se não fizeres nada, o
Google desliga e apaga o servidor e os dados.**

- Antes disso, vai a **Billing** e carrega em **Activate full account** (Ativar conta
  completa), no aviso que aparece no topo.
- O servidor continua grátis: só pagarias se usasses algo fora do plano grátis, e o alerta de
  1 € avisava-te.

## Como funciona depois

- **Atualizações:** todas as noites (por volta das 4h) o servidor vai buscar a versão mais
  recente ao GitHub. Se a versão nova não arrancar, volta sozinho à anterior.
- **Cópias de segurança:** todas as noites guarda uma cópia da base de dados no servidor
  (últimos 30 dias). De vez em quando, em **Importar → Descarregar cópia de segurança**, guarda
  também uma no iCloud.
- **Tráfego:** o plano grátis inclui 1 GB por mês de dados a sair do servidor. A FinTrack gasta
  muito menos do que isso.

## Se algo correr mal

**Entrar no servidor:** na consola do Google, em **VM instances**, carrega em **SSH** ao lado de
`fintrack`. Abre um terminal no browser.

- **A máquina não aparece na Tailscale ao fim de 15 minutos:** no terminal SSH, corre
  `sudo tail -50 /var/log/fintrack-install.log` e manda-me o que aparecer.
- **O endereço não abre:** confirma que a Tailscale está ligada no iPad e que ativaste o *HTTPS
  Certificates* (Parte 1, passo 3).
- **Esqueceste a palavra-passe:** no terminal SSH, corre `sudo rm /var/lib/fintrack/data/auth.json`
  e depois `sudo systemctl restart fintrack`. Os dados ficam; só voltas a criar a palavra-passe.

<sub>A instalação também funciona noutros servidores Ubuntu, por exemplo na Oracle Cloud: cola
o mesmo script em "Initialization script".</sub>
