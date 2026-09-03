#!/usr/bin/env python3
"""
Start the whole project with one command:

    python run.py

Runs the trading bot and the dashboard side by side, waits for the dashboard to
come up, opens it in a browser, and shuts both down cleanly on Ctrl+C.

    python run.py --no-bot          dashboard only (safe: places no orders)
    python run.py --no-dashboard    bot only
    python run.py --no-browser      don't open a browser
    python run.py --port 8000       serve the dashboard elsewhere
    python run.py --check           run the preflight checks and exit
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.error import URLError
from urllib.request import urlopen

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5123
WINDOWS = os.name == "nt"

# Run the server in-process with the reloader off. `python -m
# binance_trade_bot.api_server` hardcodes debug=True, and the reloader forks a
# child that outlives a terminate() on the parent, stranding the port.
DASHBOARD_SNIPPET = (
    "from binance_trade_bot.api_server import app, socketio; "
    "socketio.run(app, host={host!r}, port={port!r}, allow_unsafe_werkzeug=True)"
)


class Service:
    """A child process whose output is echoed with a short prefix."""

    def __init__(self, name, argv, colour):
        self.name = name
        self.argv = argv
        self.colour = colour
        self.process = None
        self.reader = None

    def start(self):
        env = dict(os.environ)
        # Binance's testnet lists a few symbols with CJK names, which blow up a
        # cp1252 console on Windows. Force UTF-8, and unbuffer so the prefixed
        # output stays in step with what is actually happening.
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        kwargs = {}
        if WINDOWS:
            # own process group, so a CTRL_BREAK can be aimed at just this child
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        self.process = subprocess.Popen(
            self.argv,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **kwargs
        )
        self.reader = threading.Thread(target=self._pump, daemon=True)
        self.reader.start()
        return self

    def _pump(self):
        prefix = "{}[{}]{} ".format(self.colour, self.name, Ansi.RESET)
        for line in self.process.stdout:
            line = line.rstrip("\r\n")
            # the scout line repaints itself with \r; keep only its last state
            if "\r" in line:
                line = line.rsplit("\r", 1)[-1]
            if line:
                # Flush each line. When this launcher's own stdout is a file
                # rather than a terminal it is block-buffered, so child output
                # would otherwise sit unseen until the buffer filled - which
                # looks exactly like a bot that has stopped doing anything.
                sys.stdout.write(prefix + line + "\n")
                sys.stdout.flush()
        self.process.stdout.close()

    @property
    def alive(self):
        return self.process is not None and self.process.poll() is None

    def stop(self, grace=10):
        """Ask nicely (so the bot can close its streams), then insist."""
        if not self.alive:
            return
        try:
            if WINDOWS:
                os.kill(self.process.pid, signal.CTRL_BREAK_EVENT)
            else:
                self.process.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            pass

        try:
            self.process.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            pass

        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()


class Ansi:
    RESET = "\033[0m"
    BOT = "\033[36m"      # cyan
    WEB = "\033[35m"      # magenta
    OK = "\033[32m"
    WARN = "\033[33m"
    ERR = "\033[31m"

    @classmethod
    def disable(cls):
        for attr in ("RESET", "BOT", "WEB", "OK", "WARN", "ERR"):
            setattr(cls, attr, "")


def say(msg, colour=""):
    sys.stdout.write("{}{}{}\n".format(colour, msg, Ansi.RESET))
    sys.stdout.flush()


def port_is_busy(host, port):
    """True if something is already listening there."""
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            return sock.connect_ex((probe_host, port)) == 0
        except OSError:
            return False


def preflight(need_bot, host=None, port=None):
    """
    Catch the setup mistakes that otherwise fail deep inside the bot, where the
    traceback says nothing useful. Returns a list of problem strings.
    """
    problems = []

    if port is not None and port_is_busy(host, port):
        finder = "netstat -ano | findstr :{}" if WINDOWS else "lsof -i :{}"
        problems.append(
            "port {} is already in use, so the dashboard cannot bind. Usually this "
            "means another run.py is still going. Stop it, or use --port <other>. "
            "To see what holds it: {}".format(port, finder.format(port))
        )

    for directory in ("logs", "data"):
        path = os.path.join(ROOT, directory)
        if not os.path.isdir(path):
            os.makedirs(path, exist_ok=True)

    if not os.path.exists(os.path.join(ROOT, "user.cfg")):
        problems.append(
            "user.cfg not found. Copy .user.cfg.example to user.cfg -- note the "
            "name has no leading dot -- and put your API keys in it."
        )
        return problems

    try:
        from binance_trade_bot.config import Config

        config = Config()
    except Exception as exc:  # pylint: disable=broad-except
        problems.append("user.cfg could not be read: {}: {}".format(type(exc).__name__, exc))
        return problems

    if not config.SUPPORTED_COIN_LIST:
        problems.append("supported_coin_list is empty, so there is nothing to trade between.")

    current = config.CURRENT_COIN_SYMBOL
    if need_bot and current and current not in config.SUPPORTED_COIN_LIST:
        problems.append(
            "current_coin={} is not in supported_coin_list. The bot exits at startup "
            "when these disagree. Add it to the list, or set current_coin to one of: "
            "{}".format(current, " ".join(config.SUPPORTED_COIN_LIST))
        )

    return problems


def wait_for_dashboard(url, process_is_alive, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not process_is_alive():
            return False
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    # Make sure this is our child answering. If the port was
                    # already taken, a stray server replies 200 while our own
                    # process is busy dying on the bind error.
                    time.sleep(0.3)
                    return process_is_alive()
        except (URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Run the trading bot and its dashboard together.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--no-bot", action="store_true", help="dashboard only; places no orders")
    parser.add_argument("--no-dashboard", action="store_true", help="bot only")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--host", default=DEFAULT_HOST, help="dashboard bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="dashboard port")
    parser.add_argument("--check", action="store_true", help="run preflight checks and exit")
    args = parser.parse_args()

    if not sys.stdout.isatty() or WINDOWS and not os.environ.get("WT_SESSION"):
        # Legacy consoles render raw escape codes; not worth the mess.
        if not os.environ.get("FORCE_COLOR"):
            Ansi.disable()

    # every module resolves user.cfg, logs/ and data/ relative to the cwd
    os.chdir(ROOT)

    run_bot = not args.no_bot
    run_web = not args.no_dashboard
    if not run_bot and not run_web:
        say("Nothing to run: --no-bot and --no-dashboard cancel each other out.", Ansi.ERR)
        return 2

    problems = preflight(run_bot, args.host if run_web else None, args.port if run_web else None)
    if problems:
        say("Preflight failed:", Ansi.ERR)
        for problem in problems:
            say("  - " + problem, Ansi.ERR)
        return 1
    say("Preflight OK.", Ansi.OK)

    if args.check:
        return 0

    services = []
    if run_web:
        snippet = DASHBOARD_SNIPPET.format(host=args.host, port=args.port)
        services.append(Service("web", [sys.executable, "-c", snippet], Ansi.WEB))
    if run_bot:
        services.append(Service("bot", [sys.executable, "-m", "binance_trade_bot"], Ansi.BOT))

    for service in services:
        service.start()
        say("started {} (pid {})".format(service.name, service.process.pid), Ansi.OK)

    url = "http://{}:{}/".format("localhost" if args.host in ("0.0.0.0", "127.0.0.1") else args.host, args.port)
    if run_web:
        web = services[0]
        if wait_for_dashboard(url, lambda: web.alive):
            say("dashboard ready at {}".format(url), Ansi.OK)
            if not args.no_browser:
                webbrowser.open(url)
        else:
            say("dashboard did not come up at {} -- see the [web] lines above.".format(url), Ansi.WARN)

    say("Ctrl+C to stop.", Ansi.OK)

    exit_code = 0
    try:
        while True:
            for service in services:
                if not service.alive:
                    code = service.process.returncode
                    # Windows reports a killed process as unsigned 0xFFFFFFFF
                    if WINDOWS and code is not None and code > 0x7FFFFFFF:
                        code -= 0x100000000
                    say("{} exited with code {}; shutting the rest down.".format(service.name, code), Ansi.WARN)
                    exit_code = code or 1
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        say("", "")
        say("stopping...", Ansi.WARN)
    finally:
        for service in reversed(services):
            service.stop()
            say("stopped {}".format(service.name), Ansi.OK)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
