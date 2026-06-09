#!/usr/bin/env python3
"""CapivaraCLI — Terminal Tamagotchi com cérebro Ollama"""

import json
import os
import sys
import argparse
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

CAPIVARA_DIR = Path.home() / ".capivara"
STATE_FILE = CAPIVARA_DIR / "state.json"
GREETING_CACHE = CAPIVARA_DIR / "greeting_cache.txt"
PROMPT_CACHE = CAPIVARA_DIR / "prompt_cache.txt"
XP_PENDING = CAPIVARA_DIR / "xp_pending.txt"
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "llama3.1:8b"

DECAY_PER_HOUR = {"hunger": 5, "happiness": 2, "energy": 3}

STAGES = [(0, "filhote"), (7, "jovem"), (30, "adulto"), (180, "anciao")]

ASCII_ARTS = {
    "filhote": """\
    .-.
   (o o)    filhote
    \\_/
    /\\
   /  \\""",

    "jovem": """\
   .--.
  (o  o)    jovem
   \\__/
  /|  |\\
  U    U""",

    "adulto": """\
  .------.
 ( o    o )  adulto
  \\  ᵕᵕ /
  /|    |\\
  U      U""",

    "anciao": """\
  .-------.
 ( ©    © )  ancião
  \\  ω   /   ~sábio
  /|     |\\
  U       U""",
}

MINI_ART = {
    "filhote": "(ᵔᴥᵔ)",
    "jovem":   "ʕ•ᴥ•ʔ",
    "adulto":  "ʕ ᵔᴥᵔ ʔ",
    "anciao":  "ʕ≧ᴥ≦ʔ",
}

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    RICH = True
    console = Console()
except ImportError:
    RICH = False
    console = None


def bar(value, length=12):
    filled = int((max(0, min(100, value)) / 100) * length)
    return "█" * filled + "░" * (length - filled)


def status_color(value):
    if value >= 70: return "green"
    if value >= 40: return "yellow"
    return "red"


def get_stage(age_days):
    stage = "filhote"
    for threshold, name in STAGES:
        if age_days >= threshold:
            stage = name
    return stage


def get_mood(state):
    avg = (state["hunger"] + state["happiness"] + state["energy"]) / 3
    if avg >= 80: return "feliz", "😊"
    if avg >= 60: return "bem", "🙂"
    if avg >= 40: return "cansado", "😐"
    if avg >= 20: return "triste", "😢"
    return "péssimo", "😰"


def load_state():
    if not STATE_FILE.exists():
        return None
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    CAPIVARA_DIR.mkdir(exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)
    # Keep prompt cache fresh — zsh reads this with `cat` (no Python overhead)
    try:
        stage = get_stage(state.get("age_days", 0))
        _, emoji = get_mood(state)
        mini = MINI_ART[stage]
        PROMPT_CACHE.write_text(f"{mini}nv{state['level']}{emoji}")
    except Exception:
        pass


def new_capivara(name="Capivara"):
    now = datetime.now().isoformat()
    return {
        "name": name,
        "born": now,
        "last_seen": now,
        "hunger": 85,
        "happiness": 85,
        "energy": 85,
        "xp": 0,
        "level": 1,
        "interactions": 0,
        "age_days": 0,
    }


def apply_decay(state):
    last = datetime.fromisoformat(state["last_seen"])
    now = datetime.now()
    hours = (now - last).total_seconds() / 3600

    state["hunger"] = max(0, state["hunger"] - DECAY_PER_HOUR["hunger"] * hours)
    state["happiness"] = max(0, state["happiness"] - DECAY_PER_HOUR["happiness"] * hours)

    # Energia: recupera à noite (22h-6h), decai durante o dia
    if 22 <= now.hour or now.hour < 6:
        state["energy"] = min(100, state["energy"] + DECAY_PER_HOUR["energy"] * hours * 2)
    else:
        state["energy"] = max(0, state["energy"] - DECAY_PER_HOUR["energy"] * hours)

    state["last_seen"] = now.isoformat()
    born = datetime.fromisoformat(state["born"])
    state["age_days"] = (now - born).days

    # Drain XP accumulated by shell preexec hook
    try:
        if XP_PENDING.exists():
            pending = int(XP_PENDING.read_text().strip() or "0")
            if pending > 0:
                state["xp"] += pending
                XP_PENDING.write_text("0")
    except Exception:
        pass

    return state


def check_levelup(state):
    new_level = (state["xp"] // 100) + 1
    if new_level > state["level"]:
        state["level"] = new_level
        msg = f"★ NÍVEL UP! {state['name']} chegou ao nível {new_level}! ★"
        if RICH:
            console.print(f"\n[bold yellow]{msg}[/bold yellow]")
        else:
            print(f"\n{msg}")


def _spin(stop_event):
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    i = 0
    while not stop_event.is_set():
        sys.stdout.write(f"\r{frames[i % len(frames)]} ")
        sys.stdout.flush()
        time.sleep(0.1)
        i += 1
    sys.stdout.write("\r  \r")
    sys.stdout.flush()


def ollama_ask(prompt, timeout=20, spin=True):
    data = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0.9, "num_predict": 50},
    }).encode()

    stop = threading.Event()
    if spin and sys.stdout.isatty():
        t = threading.Thread(target=_spin, args=(stop,), daemon=True)
        t.start()

    try:
        req = urllib.request.Request(
            OLLAMA_URL, data=data,
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            return result.get("message", {}).get("content", "").strip()
    except Exception:
        return None
    finally:
        stop.set()


def capivara_speak(state, context="greeting", user_msg=None, spin=True):
    stage = get_stage(state.get("age_days", 0))
    mood, _ = get_mood(state)
    name = state["name"]

    base = (
        f"Você é {name}, uma capivara virtual no estágio '{stage}' com humor '{mood}'. "
        "Fale sempre em português, seja fofa, calma, levemente filosófica — como capivaras são. "
        "Máximo 1-2 frases curtas."
    )

    ctxmap = {
        "greeting": f"{base} Dê boas-vindas ao usuário que abriu o terminal.",
        "hungry":   f"{base} Você está com muita fome (fome={state['hunger']:.0f}/100). Reclame fofamente.",
        "feed":     f"{base} Você acabou de comer grama e está feliz. Reaja com alegria.",
        "pet":      f"{base} Você está recebendo carinho. Reaja com carinho.",
        "talk":     f"{base} O usuário disse: '{user_msg}'. Responda na personalidade de capivara.",
        "levelup":  f"{base} Você acabou de subir de nível! Comemore.",
    }

    fallbacks = {
        "greeting": "Oink... mais um dia de grama.",
        "hungry":   "Tô com fomeee...",
        "feed":     "Nhaaaam! Grama boa!",
        "pet":      "Purrrr... ♡",
        "talk":     "... (pensando na grama)",
        "levelup":  "★ Evoluí! Mais grama pra mim!",
    }

    result = ollama_ask(ctxmap.get(context, base), spin=spin)
    return result if result else fallbacks.get(context, "...")


def print_speech(state, text, context=None):
    stage = get_stage(state.get("age_days", 0))
    mini = MINI_ART[stage]
    if RICH:
        console.print(f"[yellow]{mini}[/yellow] [italic]{text}[/italic]")
    else:
        print(f"{mini} {text}")


# ─── Comandos ───────────────────────────────────────────────────────────────

def _regen_greeting_cache(state):
    """Runs in background — generates next greeting and saves to cache."""
    ctx = "hungry" if state["hunger"] < 30 else "greeting"
    speech = capivara_speak(state, ctx, spin=False)
    if speech:
        try:
            GREETING_CACHE.write_text(speech)
        except Exception:
            pass


def cmd_greet(state):
    stage = get_stage(state.get("age_days", 0))
    mood, emoji = get_mood(state)
    mini = MINI_ART[stage]

    # Show cached speech instantly, fallback to static if no cache yet
    if GREETING_CACHE.exists():
        speech = GREETING_CACHE.read_text().strip()
    else:
        speech = "Oink... mais um dia de grama."

    if RICH:
        console.print(
            f"\n[yellow]{mini}[/yellow] [italic]{speech}[/italic]  "
            f"[dim]({state['name']} nv.{state['level']} | {emoji} {mood})[/dim]\n"
        )
    else:
        print(f"\n{mini} {speech}  ({state['name']} nv.{state['level']} | {emoji})\n")

    # Regenerate cache in background (detached — won't block terminal)
    pid = os.fork()
    if pid == 0:
        os.setsid()
        sys.stdin = open(os.devnull)
        sys.stdout = open(os.devnull, "w")
        sys.stderr = open(os.devnull, "w")
        _regen_greeting_cache(state)
        os._exit(0)


def cmd_status(state):
    stage = get_stage(state.get("age_days", 0))
    mood, emoji = get_mood(state)
    art = ASCII_ARTS[stage]

    if RICH:
        from rich.markup import escape
        h_color = status_color(state["hunger"])
        p_color = status_color(state["happiness"])
        e_color = status_color(state["energy"])

        stats = (
            f"\n[bold]{state['name']}[/bold] — {stage} — {state.get('age_days', 0)} dias\n"
            f"Humor: {emoji} {mood} | Nível {state['level']} (XP {state['xp']})\n\n"
            f"🌿 Fome       [{h_color}]{bar(state['hunger'])}[/{h_color}] {state['hunger']:.0f}%\n"
            f"💛 Felicidade [{p_color}]{bar(state['happiness'])}[/{p_color}] {state['happiness']:.0f}%\n"
            f"⚡ Energia    [{e_color}]{bar(state['energy'])}[/{e_color}] {state['energy']:.0f}%\n"
        )
        console.print(Panel(
            f"[yellow]{escape(art)}[/yellow]\n{stats}",
            title="[bold green]🐾 CapivaraCLI[/bold green]",
            border_style="green"
        ))
    else:
        print(art)
        print(f"\n{state['name']} | {stage} | {state.get('age_days',0)} dias | {emoji} {mood}")
        print(f"🌿 Fome:       [{bar(state['hunger'])}] {state['hunger']:.0f}%")
        print(f"💛 Felicidade: [{bar(state['happiness'])}] {state['happiness']:.0f}%")
        print(f"⚡ Energia:    [{bar(state['energy'])}] {state['energy']:.0f}%")
        print(f"Nível: {state['level']} | XP: {state['xp']}")


def cmd_feed(state):
    gain = random.randint(15, 25)
    state["hunger"] = min(100, state["hunger"] + gain)
    state["happiness"] = min(100, state["happiness"] + 5)
    state["xp"] += 10
    speech = capivara_speak(state, "feed")
    print_speech(state, speech)
    if RICH:
        console.print(f"[dim](+{gain} fome, +5 felicidade, +10 XP)[/dim]")
    else:
        print(f"(+{gain} fome, +5 felicidade, +10 XP)")
    check_levelup(state)


def cmd_pet(state):
    state["happiness"] = min(100, state["happiness"] + 20)
    state["xp"] += 5
    speech = capivara_speak(state, "pet")
    stage = get_stage(state.get("age_days", 0))
    mini = "ʕ♡ᴥ♡ʔ"
    if RICH:
        console.print(f"[magenta]{mini}[/magenta] [italic]{speech}[/italic]  [dim](+20 felicidade, +5 XP)[/dim]")
    else:
        print(f"{mini} {speech}  (+20 felicidade, +5 XP)")
    check_levelup(state)


def cmd_talk(state):
    if RICH:
        user_msg = console.input("[bold cyan]Você:[/bold cyan] ")
    else:
        user_msg = input("Você: ")

    if not user_msg.strip():
        return

    speech = capivara_speak(state, "talk", user_msg=user_msg)
    state["xp"] += 3
    state["happiness"] = min(100, state["happiness"] + 3)
    print_speech(state, speech)
    check_levelup(state)


def cmd_new(state):
    if state is not None:
        if RICH:
            confirm = console.input(
                f"[yellow]Criar nova capivara? A atual ([bold]{state['name']}[/bold], "
                f"{state.get('age_days', 0)} dias) será perdida. (s/N):[/yellow] "
            )
        else:
            confirm = input(
                f"Criar nova capivara? A atual ({state['name']}, "
                f"{state.get('age_days', 0)} dias) será perdida. (s/N): "
            )
        if confirm.strip().lower() != "s":
            print("Cancelado.")
            return None
    return True


# ─── Info Commands ───────────────────────────────────────────────────────────

def _get_active_iface():
    """Returns (iface, local_ip, gateway, subnet_cidr) from default route."""
    route = _run(["ip", "route", "show", "default"])
    iface = re.search(r'dev (\S+)', route)
    gw    = re.search(r'via (\S+)', route)
    iface = iface.group(1) if iface else None
    gw    = gw.group(1) if gw else None
    if not iface:
        return None, None, None, None
    try:
        ip_json = _run(["ip", "-j", "addr", "show", iface])
        data = json.loads(ip_json)
        for entry in data:
            for addr in entry.get("addr_info", []):
                if addr.get("family") == "inet":
                    local_ip = addr["local"]
                    prefix   = addr["prefixlen"]
                    # Build subnet CIDR
                    parts = [int(x) for x in local_ip.split(".")]
                    mask  = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
                    net   = ".".join(str((parts[i] & ((mask >> (8*(3-i))) & 0xFF))) for i in range(4))
                    subnet = f"{net}/{prefix}"
                    return iface, local_ip, gw, subnet
    except Exception:
        pass
    return iface, None, gw, None

def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def cmd_clima(_state=None):
    try:
        req = urllib.request.Request(
            "http://wttr.in/?format=j1",
            headers={"User-Agent": "curl/7.0"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        cur = data["current_condition"][0]
        area = data.get("nearest_area", [{}])[0]
        city = area.get("areaName", [{}])[0].get("value", "?")
        country = area.get("country", [{}])[0].get("value", "")

        temp     = cur["temp_C"]
        feels    = cur["FeelsLikeC"]
        desc     = cur["weatherDesc"][0]["value"]
        humidity = cur["humidity"]
        wind     = cur["windspeedKmph"]
        vis      = cur["visibility"]

        weather_icon = {
            "Sunny": "☀️", "Clear": "🌙", "Partly cloudy": "⛅",
            "Cloudy": "☁️", "Overcast": "☁️", "Mist": "🌫️",
            "Rain": "🌧️", "Drizzle": "🌦️", "Thunder": "⛈️",
            "Snow": "❄️", "Fog": "🌫️", "Blizzard": "🌨️",
        }.get(desc, "🌡️")

        lines = [
            f"[bold]{weather_icon} {desc}[/bold]   {city}, {country}",
            "",
            f"🌡️  Temperatura:  [cyan]{temp}°C[/cyan]  (sensação {feels}°C)",
            f"💧 Humidade:     [blue]{humidity}%[/blue]",
            f"💨 Vento:        [green]{wind} km/h[/green]",
            f"👁️  Visibilidade: {vis} km",
        ]
        # 3-day forecast
        try:
            days = data.get("weather", [])[:3]
            lines.append("")
            lines.append("[bold]Próximos 3 dias:[/bold]")
            for d in days:
                date = d["date"]
                mn = d["mintempC"]
                mx = d["maxtempC"]
                ddesc = d["hourly"][4]["weatherDesc"][0]["value"]
                lines.append(f"  {date}  {mn}°↑{mx}°  {ddesc}")
        except Exception:
            pass

        if RICH:
            from rich.panel import Panel
            console.print(Panel("\n".join(lines), title="[bold cyan]🌦️  CapivaraClima[/bold cyan]", border_style="cyan"))
        else:
            for l in lines:
                print(re.sub(r'\[.*?\]', '', l))

    except Exception as e:
        msg = f"Sem conexão ou wttr.in fora do ar. ({e})"
        if RICH: console.print(f"[red]{msg}[/red]")
        else: print(msg)


def cmd_tech(_state=None):
    lines = []

    # CPU load
    try:
        load = Path("/proc/loadavg").read_text().split()
        l1, l5, l15 = load[0], load[1], load[2]
        cores = os.cpu_count() or 1
        pct = float(l1) / cores * 100
        color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
        lines.append(f"[bold]CPU[/bold]  load {l1} / {l5} / {l15}  [{color}]{pct:.0f}%[/{color}] ({cores} cores)")
    except Exception:
        lines.append("CPU  n/a")

    # RAM
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            mem[k.strip()] = int(v.strip().split()[0])
        total = mem["MemTotal"] // 1024
        avail = mem["MemAvailable"] // 1024
        used  = total - avail
        pct   = used / total * 100
        color = "green" if pct < 60 else "yellow" if pct < 85 else "red"
        lines.append(f"[bold]RAM[/bold]  {used} / {total} MB  [{color}]{pct:.0f}%[/{color}]")
    except Exception:
        lines.append("RAM  n/a")

    # Disk
    try:
        usage = shutil.disk_usage("/")
        total = usage.total // (1024**3)
        used  = usage.used  // (1024**3)
        pct   = usage.used / usage.total * 100
        color = "green" if pct < 70 else "yellow" if pct < 90 else "red"
        lines.append(f"[bold]Disk[/bold] {used} / {total} GB  [{color}]{pct:.0f}%[/{color}]")
    except Exception:
        lines.append("Disk n/a")

    # GPU (nvidia-smi)
    gpu_out = _run(["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total",
                    "--format=csv,noheader,nounits"], timeout=4)
    if gpu_out:
        for row in gpu_out.splitlines():
            parts = [p.strip() for p in row.split(",")]
            if len(parts) >= 5:
                name, temp, util, mem_used, mem_total = parts[:5]
                color = "green" if int(util) < 60 else "yellow" if int(util) < 85 else "red"
                lines.append(f"[bold]GPU[/bold]  {name}  🌡️{temp}°C  [{color}]{util}%[/{color}]  {mem_used}/{mem_total} MB")

    # Docker
    docker_out = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"], timeout=5)
    if docker_out:
        lines.append("")
        lines.append("[bold]🐳 Docker containers:[/bold]")
        for row in docker_out.splitlines():
            parts = row.split("\t")
            if len(parts) >= 3:
                name, status, image = parts[0], parts[1], parts[2]
                up = status.startswith("Up")
                icon = "🟢" if up else "🔴"
                lines.append(f"  {icon} [bold]{name}[/bold]  {status}  [dim]{image}[/dim]")
    else:
        lines.append("[dim]Docker: sem containers rodando (ou não instalado)[/dim]")

    if RICH:
        from rich.panel import Panel
        console.print(Panel("\n".join(lines), title="[bold green]🖥️  CapivaraTech[/bold green]", border_style="green"))
    else:
        for l in lines:
            print(re.sub(r'\[.*?\]', '', l))


def cmd_net(_state=None):
    lines = []

    # Interfaces + VPN detection
    vpn_ifaces = []
    try:
        ip_json = _run(["ip", "-j", "addr"])
        ifaces = json.loads(ip_json) if ip_json else []
        vpn_keywords = {"tun", "wg", "vpn", "ppp", "tap", "nordlynx", "proton"}
        skip_prefixes = {"veth", "br-"}  # Docker internals — mostrar só contagem
        lines.append("[bold]Interfaces:[/bold]")
        veth_count = 0
        for iface in ifaces:
            name  = iface.get("ifname", "")
            state = iface.get("operstate", "")
            addrs = [a["local"] for a in iface.get("addr_info", []) if "local" in a]
            if any(name.startswith(p) for p in skip_prefixes):
                veth_count += 1
                continue
            if state == "UNKNOWN" and not addrs:
                continue
            is_vpn = any(kw in name.lower() for kw in vpn_keywords)
            if is_vpn:
                vpn_ifaces.append(name)
            icon = "🔐" if is_vpn else ("🟢" if state == "UP" else "⚪")
            addr_str = "  ".join(addrs) if addrs else "sem IP"
            color = "magenta" if is_vpn else ("green" if state == "UP" else "dim")
            lines.append(f"  {icon} [{color}]{name}[/{color}]  {addr_str}")
        if veth_count:
            lines.append(f"  [dim]+ {veth_count} interfaces Docker (veth/br) ocultas[/dim]")
    except Exception:
        lines.append("  Interfaces: n/a")

    # VPN status summary
    if vpn_ifaces:
        lines.append(f"\n[bold magenta]🔐 VPN ATIVA:[/bold magenta] {', '.join(vpn_ifaces)}")
    else:
        lines.append("\n[dim]VPN: não detectada[/dim]")

    # Default gateway
    gw = _run(["ip", "route", "show", "default"])
    if gw:
        lines.append(f"[bold]Gateway:[/bold] {gw.splitlines()[0]}")

    # Public IP (com timeout curto)
    lines.append("")
    try:
        req = urllib.request.Request("https://ifconfig.me", headers={"User-Agent": "curl/7.0"})
        with urllib.request.urlopen(req, timeout=4) as resp:
            pub_ip = resp.read().decode().strip()
        lines.append(f"[bold]IP Público:[/bold]  [cyan]{pub_ip}[/cyan]")
    except Exception:
        lines.append("[bold]IP Público:[/bold]  [dim]timeout[/dim]")

    # DNS
    try:
        resolv = Path("/etc/resolv.conf").read_text()
        dns = [l.split()[1] for l in resolv.splitlines() if l.startswith("nameserver")]
        lines.append(f"[bold]DNS:[/bold]         {', '.join(dns)}")
    except Exception:
        pass

    # Ping google
    ping = _run(["ping", "-c", "1", "-W", "2", "8.8.8.8"], timeout=4)
    if "time=" in ping:
        ms = re.search(r"time=([\d.]+)", ping)
        latency = ms.group(1) if ms else "?"
        lines.append(f"[bold]Latência:[/bold]    [green]{latency} ms[/green] (8.8.8.8)")
    else:
        lines.append("[bold]Latência:[/bold]    [red]sem resposta[/red]")

    if RICH:
        from rich.panel import Panel
        console.print(Panel("\n".join(lines), title="[bold blue]🌐 CapivaraNet[/bold blue]", border_style="blue"))
    else:
        for l in lines:
            print(re.sub(r'\[.*?\]', '', l))


def cmd_hacker(_state=None):
    lines = []

    # Portas abertas (ss -tlnp)
    ss_out = _run(["ss", "-tlnp"])
    if ss_out:
        seen = {}
        for row in ss_out.splitlines()[1:]:
            parts = row.split()
            if len(parts) >= 4:
                local = parts[3]
                proc  = parts[6] if len(parts) > 6 else ""
                port  = local.rsplit(":", 1)[-1] if ":" in local else local
                if not port.isdigit():
                    continue
                name = re.search(r'\"(.+?)\"', proc)
                pname = name.group(1) if name else "?"
                if port not in seen:
                    seen[port] = pname
        ports = list(seen.items())
        lines.append(f"[bold]🔌 Portas abertas ({len(ports)}):[/bold]")
        known_ports = {
            "22": "SSH", "80": "HTTP", "443": "HTTPS", "3306": "MySQL",
            "5432": "PostgreSQL", "6379": "Redis", "8080": "HTTP-alt",
            "8089": "API", "9000": "Portainer", "11434": "Ollama",
            "27017": "MongoDB", "7200": "GraphDB", "5180": "UI",
            "3001": "Node", "631": "CUPS",
        }
        for port, name in sorted(ports, key=lambda x: int(x[0])):
            known = known_ports.get(port, "")
            label = f"[dim]{known}[/dim]" if known else ""
            lines.append(f"  :{port}  {name}  {label}")
    else:
        lines.append("[dim]ss não disponível[/dim]")

    # SUID binários não-padrão
    lines.append("")
    suid_out = _run(["find", "/usr/bin", "/usr/local/bin", "-perm", "-4000", "-type", "f"], timeout=8)
    expected_suid = {
        "sudo", "su", "passwd", "newgrp", "chsh", "chfn", "gpasswd", "mount", "umount",
        "pkexec", "unix_chkpwd", "chage", "expiry", "sg", "fusermount", "fusermount3",
        "mount.cifs", "crontab", "ksu", "nvidia-modprobe", "Xorg", "dbus-daemon-launch-helper",
        "pam_timestamp_check", "at", "ssh-keysign", "ping",
    }
    if suid_out:
        suids = suid_out.splitlines()
        unusual = [s for s in suids if Path(s).name not in expected_suid]
        lines.append(f"[bold]🔑 SUID binários:[/bold] {len(suids)} encontrados")
        if unusual:
            for u in unusual:
                lines.append(f"  [yellow]⚠️  {u}[/yellow]")
        else:
            lines.append("  [green]✓ Nenhum incomum[/green]")

    # SSH falhas recentes
    lines.append("")
    ssh_fails = _run(["journalctl", "-u", "sshd", "--no-pager", "-n", "100",
                      "--since", "24 hours ago"], timeout=5)
    if ssh_fails:
        fail_lines = [l for l in ssh_fails.splitlines() if "Failed" in l or "Invalid" in l]
        ips = re.findall(r'from (\d+\.\d+\.\d+\.\d+)', " ".join(fail_lines))
        from collections import Counter
        top = Counter(ips).most_common(5)
        lines.append(f"[bold]🚨 SSH falhas (24h):[/bold] {len(fail_lines)} tentativas")
        if top:
            for ip, count in top:
                lines.append(f"  [red]{ip}[/red]  {count}x")
        else:
            lines.append("  [green]✓ Nenhuma[/green]")
    else:
        lines.append("[bold]🚨 SSH falhas:[/bold]  [green]✓ Nenhuma / sshd inativo[/green]")

    # Firewall
    lines.append("")
    ufw = _run(["ufw", "status"], timeout=3)
    if ufw:
        status = "ativo" if "active" in ufw.lower() else "inativo"
        color = "green" if status == "ativo" else "red"
        lines.append(f"[bold]🛡️  Firewall (ufw):[/bold] [{color}]{status}[/{color}]")
    else:
        # Tenta iptables
        ipt = _run(["iptables", "-L", "INPUT", "--line-numbers", "-n"], timeout=3)
        if ipt:
            rules = len([l for l in ipt.splitlines() if l[0].isdigit()])
            lines.append(f"[bold]🛡️  iptables INPUT:[/bold] {rules} regras")
        else:
            lines.append("[bold]🛡️  Firewall:[/bold] [dim]não detectado[/dim]")

    # World-writable em /etc
    ww = _run(["find", "/etc", "-maxdepth", "2", "-writable", "-not", "-user", "root",
               "-not", "-type", "l"], timeout=6)
    if ww:
        lines.append(f"\n[bold]⚠️  Arquivos /etc graváveis por não-root:[/bold]")
        for f in ww.splitlines()[:5]:
            lines.append(f"  [red]{f}[/red]")
    else:
        lines.append("\n[bold]/etc gravável:[/bold] [green]✓ Limpo[/green]")

    if RICH:
        from rich.panel import Panel
        console.print(Panel("\n".join(lines), title="[bold red]🕵️  CapivaraHacker[/bold red]", border_style="red"))
    else:
        for l in lines:
            print(re.sub(r'\[.*?\]', '', l))


def cmd_scan(_state=None):
    from collections import defaultdict

    lines  = []
    risks  = []   # (level, msg)  level: "low"|"medium"|"high"|"critical"

    iface, local_ip, gateway, subnet = _get_active_iface()

    if not iface:
        msg = "Não foi possível detectar interface ativa."
        if RICH: console.print(f"[red]{msg}[/red]")
        else: print(msg)
        return

    lines.append(f"[bold]Interface:[/bold] {iface}  IP: [cyan]{local_ip}[/cyan]  Gateway: [cyan]{gateway}[/cyan]")
    lines.append(f"[bold]Subnet:[/bold]    {subnet}")
    lines.append("")

    # ── 1. Host discovery (nmap -sn) ─────────────────────────────────────────
    if not shutil.which("nmap"):
        lines.append("[yellow]⚠  nmap não instalado (pacman -S nmap)[/yellow]")
        risks.append(("low", "nmap ausente — instale para scans completos"))
    else:
        if RICH: console.print(f"[dim]🔍 Descobrindo hosts em {subnet} …[/dim]", end="")
        nmap_hosts = _run(["nmap", "-sn", "--min-rate", "1000", "-T4", subnet], timeout=45)
        if RICH: console.print("\r" + " " * 50 + "\r", end="")

        hosts = re.findall(r'Nmap scan report for (.+)', nmap_hosts)
        macs  = re.findall(r'MAC Address: ([0-9A-F:]{17})(?: \((.+?)\))?', nmap_hosts)

        lines.append(f"[bold]🖥️  Hosts descobertos ({len(hosts)}):[/bold]")
        vendor_map = {}
        for i, host in enumerate(hosts):
            mac, vendor = (macs[i-1] if i > 0 and i-1 < len(macs) else ("?", ""))
            vendor_map[host] = vendor
            is_gw = gateway and (gateway in host or host == gateway)
            label = " [yellow](gateway)[/yellow]" if is_gw else ""
            lines.append(f"  {'🌐' if is_gw else '💻'} {host}  [dim]{mac}  {vendor}[/dim]{label}")

        if len(hosts) > 20:
            risks.append(("medium", f"{len(hosts)} hosts na rede — rede muito populada (hotel/coworking?)"))
        elif len(hosts) > 50:
            risks.append(("high", f"{len(hosts)} hosts — alto risco em rede pública"))

        # ── 2. Gateway port scan (nmap -F) ────────────────────────────────────
        if gateway:
            if RICH: console.print(f"[dim]🔍 Escaneando gateway {gateway} …[/dim]", end="")
            gw_scan = _run(["nmap", "-F", "--open", "-T4", gateway], timeout=30)
            if RICH: console.print("\r" + " " * 50 + "\r", end="")

            gw_ports = re.findall(r'(\d+)/tcp\s+open\s+(\S+)', gw_scan)
            suspicious_gw = {"23", "21", "8080", "8443", "9090", "4444", "1234"}

            lines.append(f"\n[bold]🌐 Gateway {gateway} — portas abertas:[/bold]")
            if gw_ports:
                for port, svc in gw_ports:
                    flag = " [red]⚠ SUSPEITO[/red]" if port in suspicious_gw else ""
                    lines.append(f"  :{port}  {svc}{flag}")
                    if port in suspicious_gw:
                        risks.append(("high", f"Gateway expõe porta suspeita :{port} ({svc})"))
                if len(gw_ports) > 5:
                    risks.append(("medium", f"Gateway com {len(gw_ports)} portas abertas — roteador mal configurado"))
            else:
                lines.append("  [green]✓ Nenhuma porta aberta detectada[/green]")

    # ── 3. ARP spoofing detection (tshark) ───────────────────────────────────
    lines.append("")
    tshark_bin = shutil.which("tshark") or shutil.which("dumpcap")
    if not tshark_bin:
        lines.append("[yellow]⚠  tshark não instalado (pacman -S wireshark-cli)[/yellow]")
        risks.append(("low", "tshark ausente — instale para detecção ARP spoofing"))
    else:
        secs = 8
        if RICH: console.print(f"[dim]📡 Capturando tráfego ARP por {secs}s …[/dim]", end="")
        # Captura ARP: ip src, mac src, opcode (1=request, 2=reply)
        tshark_out = _run([
            "tshark", "-i", iface, "-a", f"duration:{secs}",
            "-Y", "arp", "-T", "fields",
            "-e", "arp.src.proto_ipv4",
            "-e", "arp.src.hw_mac",
            "-e", "arp.opcode",
            "-E", "separator=|",
        ], timeout=secs + 5)
        if RICH: console.print("\r" + " " * 50 + "\r", end="")

        ip_to_macs  = defaultdict(set)
        arp_replies = 0
        arp_counts  = defaultdict(int)

        for row in tshark_out.splitlines():
            parts = row.split("|")
            if len(parts) < 3:
                continue
            src_ip, src_mac, opcode = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if src_ip and src_mac:
                ip_to_macs[src_ip].add(src_mac)
            if opcode == "2":
                arp_replies += 1
                arp_counts[src_ip] += 1

        spoofed = {ip: macs for ip, macs in ip_to_macs.items() if len(macs) > 1}

        lines.append(f"[bold]📡 Análise ARP ({secs}s):[/bold]")
        lines.append(f"  Replies capturados: {arp_replies}")
        lines.append(f"  IPs únicos vistos:  {len(ip_to_macs)}")

        # ARP storm detection
        for ip, count in arp_counts.items():
            if count > 20:
                lines.append(f"  [red]⚡ ARP STORM: {ip} enviou {count} replies![/red]")
                risks.append(("critical", f"ARP storm detectado de {ip} — possível ataque"))

        # ARP spoofing detection
        if spoofed:
            lines.append(f"\n  [bold red]🚨 ARP SPOOFING DETECTADO![/bold red]")
            for ip, macs in spoofed.items():
                is_gw = gateway and ip == gateway
                tag = " [red](GATEWAY!)[/red]" if is_gw else ""
                lines.append(f"  [red]  {ip} → {', '.join(macs)}{tag}[/red]")
                severity = "critical" if is_gw else "high"
                risks.append((severity, f"ARP spoofing: {ip} tem {len(macs)} MACs diferentes — possível MITM"))
        else:
            lines.append("  [green]✓ Sem ARP spoofing detectado[/green]")

        # Verifica se gateway tem MAC consistente com o que vimos no nmap
        if gateway and gateway in ip_to_macs and len(ip_to_macs[gateway]) == 1:
            lines.append(f"  [green]✓ Gateway {gateway} MAC consistente[/green]")

    # ── 4. Risk summary ───────────────────────────────────────────────────────
    lines.append("")
    if not risks:
        lines.append("[bold green]✅ REDE SEGURA — nenhum risco detectado[/bold green]")
    else:
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        risks.sort(key=lambda x: order.get(x[0], 9))
        color_map = {"critical": "red", "high": "orange3", "medium": "yellow", "low": "dim"}
        icon_map  = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "⚪"}
        lines.append("[bold]⚠️  Riscos detectados:[/bold]")
        for level, msg in risks:
            c = color_map.get(level, "white")
            i = icon_map.get(level, "•")
            lines.append(f"  {i} [{c}]{level.upper()}[/{c}]  {msg}")

    if RICH:
        from rich.panel import Panel
        console.print(Panel("\n".join(lines), title="[bold red]🔬 CapivaraScan[/bold red]", border_style="red"))
    else:
        for l in lines:
            print(re.sub(r'\[.*?\]', '', l))


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    global OLLAMA_MODEL
    parser = argparse.ArgumentParser(
        description="CapivaraCLI — Tamagotchi Terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Comandos:
  (nenhum)  saudação rápida ao abrir terminal
  status    estatísticas completas com ASCII art
  food      alimentar (+fome +xp)
  pet       dar carinho (+felicidade +xp)
  talk      conversar via Ollama
  new       criar nova capivara"""
    )
    parser.add_argument("command", nargs="?", default="greet",
                        choices=["greet", "status", "food", "pet", "talk", "new",
                                 "clima", "tech", "net", "hacker", "scan"])
    parser.add_argument("--name", default="Capivara")
    parser.add_argument("--model", default=OLLAMA_MODEL)

    args = parser.parse_args()
    OLLAMA_MODEL = args.model

    state = load_state()

    if args.command == "new":
        proceed = cmd_new(state)
        if proceed is None:
            return
        state = new_capivara(args.name)
        save_state(state)
        msg = f"✨ {state['name']} nasceu! Bem-vinda ao terminal!"
        if RICH:
            console.print(f"[bold green]{msg}[/bold green]")
        else:
            print(msg)
        state = apply_decay(state)
    elif state is None:
        state = new_capivara(args.name)
        save_state(state)
        msg = f"✨ Primeira vez! {state['name']} nasceu. Use 'capivara status' para ver ela."
        if RICH:
            console.print(f"[bold green]{msg}[/bold green]")
        else:
            print(msg)
        state = apply_decay(state)
    else:
        state = apply_decay(state)

    dispatch = {
        "greet":  cmd_greet,
        "status": cmd_status,
        "food":   cmd_feed,
        "pet":    cmd_pet,
        "talk":   cmd_talk,
        "clima":  cmd_clima,
        "tech":   cmd_tech,
        "net":    cmd_net,
        "hacker": cmd_hacker,
        "scan":   cmd_scan,
    }

    if args.command in dispatch:
        dispatch[args.command](state)

    state["interactions"] += 1
    save_state(state)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stage = get_stage(load_state().get("age_days", 0) if load_state() else 0)
        print(f"\n{MINI_ART.get(stage, '(ᵔᴥᵔ)')} *cochila*")
