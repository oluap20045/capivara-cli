#!/usr/bin/env python3
"""CapivaraCLI — Terminal Tamagotchi com cérebro Ollama"""

import json
import os
import sys
import argparse
import random
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
                        choices=["greet", "status", "food", "pet", "talk", "new"])
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
