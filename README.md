<div align="center">

```
                          -- ___//\
                         (  \)#####==-.
                        #(\  //       `-._
                     ###/// /  ( ● )      \
                    ####////(..            \
            ____###  ////(::              (  \
      ######@@@@@@@@ @   :@)().,          (< )
    #####@@@@@@,,,,,,,,//     ::():::      (  )
   ####@@@().+//,,,,,  /"    ::())+++++/+====/`\
  ####@(),,--,-//,,,,,        ;;.  ::::::++/
 ####(),.+,,,-.//,+,,-,        :;   ,|;|;)$
####@:+..---+-+=/,,,,,          ::::;|;|)$
###@_ .-====-,_ .-=+;,,,/         ..+=$)
####(@@@@@@())_    :,,:(/        /,,|)
##########@@@))\     ,,::(/      /,,)/
###########@@@@)): -####\(      / ///
##############@@@))##########\  / /# |
/##@@@#@####@@@@)/##########/\  )\  |
:/############//#@@@@@@#,,-"  \  |  |
"(_|_),,,,,//___-=(_|_)   \   \ |  \_
 ".,,//_____ \\            \  \_| \ _\
   _________(_/ \__________/ /_|_\______
```

# 🐾 CapivaraCLI

**Um tamagotchi de capivara que vive no seu terminal — com cérebro de LLM local.**

</div>

---

CapivaraCLI é uma capivara virtual que mora no seu shell. Ela tem fome, felicidade e energia que decaem com o tempo real, sobe de nível conforme você usa o terminal, aprende *skills* a partir dos comandos que você roda, e fala com você usando um modelo Ollama local (`llama3.1:8b`).

Bônus: vem com um arsenal de comandos de **info & segurança** — clima, monitoramento de sistema, rede, auditoria de host e scan de rede (nmap + detecção de ARP spoofing).

## ✨ Features

- **Tamagotchi de verdade** — fome, felicidade e energia decaem em tempo real. Energia recupera de noite (22h–6h) e drena de dia; o café é a única fonte líquida de energia.
- **Cérebro Ollama** — a capivara fala em português com personalidade (calma, fofa, levemente filosófica). Funciona offline com fallbacks fixos se o Ollama não estiver rodando.
- **Sistema de níveis e XP** — progressão quadrática (`nível = √(XP/25) + 1`). Cada comando que você roda no shell vira XP.
- **Skills** — a capivara aprende com o que você usa: `git`/`docker` → DevOps, `vim`/`nvim` → Editor, `python`/`node` → Programador, `nmap`/`ssh` → Network, etc. Cada skill tem títulos por nível (de *Script Kiddie* a *Full-Stack God*).
- **Estágios de vida** — filhote (0d) → jovem (7d) → adulto (30d) → ancião (180d), cada um com ASCII art própria.
- **Integração zsh zero-latência** — saudação no boot do shell via cache, emoji de humor no `RPROMPT`, XP contado em puro shell no `preexec`.
- **Dashboards info/hacker** — clima, sistema (CPU/RAM/GPU/Docker), rede (VPN/IP público/DNS), auditoria de host e scan de rede.

## 📦 Requisitos

| Item | Obrigatório? | Para quê |
|------|-------------|----------|
| Python 3.8+ | ✅ | tudo |
| [`rich`](https://github.com/Textualize/rich) | recomendado | painéis e cores (degrada pra texto puro sem ele) |
| [Ollama](https://ollama.com) + `llama3.1:8b` | opcional | falas dinâmicas da capivara (offline usa fallbacks) |
| `nmap` | opcional | comando `scan` |
| `tshark` (wireshark-cli) | opcional | detecção de ARP spoofing no `scan` |
| zsh | opcional | integração de prompt/XP automática |

> **Linux & macOS** — roda nos dois. No macOS os comandos de sistema/rede usam as ferramentas BSD nativas (`sysctl`, `vm_stat`, `ifconfig`, `lsof`, `scutil`, `route`, `socketfilterfw`); `nmap`/`tshark` instalam via `brew install nmap wireshark`. O tamagotchi e o `clima` funcionam 100% nas duas plataformas.

## 🚀 Instalação

```bash
# 1. Clonar
git clone https://github.com/oluap20045/capivara-cli.git ~/.capivara

# 2. Dependência Python (opcional mas recomendada)
pip install rich  # ou: pip install --break-system-packages rich

# 3. Comando global (symlink no PATH)
chmod +x ~/.capivara/capivara.py
ln -s ~/.capivara/capivara.py ~/.local/bin/capivara
# garanta que ~/.local/bin está no seu PATH

# 4. (Opcional) cérebro LLM
ollama pull llama3.1:8b

# 5. Nasça sua capivara
capivara new
capivara status
```

### Integração com zsh (opcional)

Adicione ao final do seu `~/.zshrc` para saudação no boot, emoji de humor no prompt e XP automático por comando:

```zsh
# CapivaraCLI
capivara greet 2>/dev/null

# Prompt: lê cache (~/.capivara/prompt_cache.txt) — zero latência
_capivara_prompt() {
    local f="$HOME/.capivara/prompt_cache.txt"
    [[ -f "$f" ]] && echo -n " $(cat $f)" || true
}

# XP: conta cada comando executado, a capivara drena na próxima chamada
_capivara_preexec() {
    local f_xp="$HOME/.capivara/xp_pending.txt"
    local f_cmd="$HOME/.capivara/cmd_pending.txt"
    local n=$(cat "$f_xp" 2>/dev/null || echo 0)
    [[ "$1" != capivara* ]] && echo $((n + 1)) > "$f_xp"
    if [[ "$1" != capivara* ]]; then
        local cmd="${1%% *}"
        echo "$cmd" >> "$f_cmd"
    fi
}
autoload -Uz add-zsh-hook
add-zsh-hook preexec _capivara_preexec
RPROMPT='$(_capivara_prompt)'"${RPROMPT}"
```

## 🎮 Comandos

### 🐾 Tamagotchi

| Comando | Efeito |
|---------|--------|
| `capivara greet` | Saudação rápida ao abrir o terminal (instantâneo via cache) |
| `capivara status` | Painel completo: ASCII art, stats, nível, XP, skills |
| `capivara food` | Alimentar (+15–25 fome, +5 felicidade, +10 XP) |
| `capivara pet` | Carinho (+20 felicidade, +5 XP) |
| `capivara cafe` | Café (+25–40 energia, +5 felicidade, +5 XP) — única fonte líquida de energia |
| `capivara talk` | Conversa livre via Ollama |
| `capivara new` | Criar nova capivara (apaga o estado atual) |

### 📡 Info & Segurança

| Comando | O que faz |
|---------|-----------|
| `capivara clima` | Clima atual + previsão 3 dias (wttr.in, localiza por IP) |
| `capivara tech` | CPU / RAM / Disk / GPU (nvidia-smi) + containers Docker |
| `capivara net` | Interfaces, detecção de VPN, IP público, DNS, latência |
| `capivara hacker` | Portas abertas, SUID incomuns, falhas SSH (24h), firewall, `/etc` gravável |
| `capivara scan` | Scan da rede local: host discovery (nmap), portas do gateway, detecção de ARP spoofing (tshark) |
| `capivara help` | Menu de todos os comandos |

### Flags

```bash
capivara talk --model qwen3:8b   # troca o modelo Ollama
capivara new  --name Tobias      # nomeia sua capivara
```

> **Nota sobre os comandos `hacker`/`scan`:** são ferramentas de auditoria **defensiva** da sua própria máquina/rede. `scan` dispara tráfego nmap/tshark na sua subnet local — use apenas em redes que você tem autorização para inspecionar.

## 🧠 Como funciona

- **Estado** persiste em `~/.capivara/state.json` (JSON simples).
- **Decaimento** é calculado on-demand a cada chamada com base em `last_seen`, hora a hora — então fechar o terminal por horas faz a capivara realmente sentir o tempo passar.
- **Saudação instantânea**: `greet` mostra a fala em cache e regenera a próxima em background via `os.fork()`, sem travar o shell.
- **Sem dependências pesadas**: só `rich` (opcional). Toda rede usa `urllib` da stdlib; o resto é `subprocess` em ferramentas que você já tem.

## 📂 Estrutura

```
~/.capivara/
├── capivara.py        # tudo aqui (~1200 linhas, single-file)
├── capivara.txt       # ASCII art de referência
├── .gitignore         # ignora estado e caches
└── state.json         # gerado em runtime (gitignored)
```

## 📜 Licença

MIT — use, modifique e compartilhe à vontade. 🐾

---

<div align="center">
<sub>Feita com grama e cafezinho. ʕ•ᴥ•ʔ</sub>
</div>
