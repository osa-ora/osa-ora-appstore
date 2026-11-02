# Osa Ora App Store

A polished, browser launchpad for your local applications.

<img width="1335" height="847" alt="Screenshot 2026-11-02 at 10 27 35 PM" src="https://github.com/user-attachments/assets/654bdcc1-3153-4c92-94d8-dde741f1e6b0" />


## Included applications

The launcher is preconfigured with four entries:

- My Private Space
- IPTV
- Demo Management
- Book Ads Animator

Their starter folders and URLs are examples. Update each application's folder path, URL, and lifecycle commands from **Settings** to match your machine. Until the configured folder exists, its status is shown as **Unavailable**.

## Requirements

- Python 3.10+
- macOS, Linux, or Windows
- A modern browser

## One-command lifecycle

Use the same command for all lifecycle operations:

### macOS / Linux

```bash
./run.sh start
./run.sh status
./run.sh stop
```

### Windows

```bat
run.bat start
run.bat status
run.bat stop
```

`start` creates the virtual environment if needed, installs dependencies, starts the launcher in the background, waits until it is ready, and opens the launcher URL in your default browser. `status` checks both the process and HTTP endpoint. `stop` terminates the launcher process.

The **×** button in the launcher header asks for confirmation, displays a browser alert, sends an explicit shutdown request, and terminates the launcher background process. It then attempts to close the current tab. Browser security may block scripts from closing tabs opened directly by the operating system; if that happens, the launcher displays a shutdown confirmation page and you can close the tab manually. Reloading or closing a browser tab does not stop the server; use the **×** button or `run.sh stop` / `run.bat stop` to stop it deliberately.

Default URL: http://127.0.0.1:8780

## Configure applications

Open **Settings → Manage applications → Edit** and set:

- Application folder (absolute path or path relative to this launcher folder)
- Application URL (used to open the app in a new tab and probe its status)
- Start command (required; any shell command, executed in the application folder)
- Stop command (optional; if omitted, the launcher terminates the process group/tree created for the start command)
- Status command (optional; exit code 0 means running, non-zero means stopped; if omitted, URL reachability is used)
- Icon path/URL
- Visibility

The launcher shows green for reachable applications, red for stopped applications whose folder exists, and grey for unavailable folders. Commands are executed by the local shell with the application folder as the working directory. Relative application paths are resolved from the launcher folder. Commands are user-configured and should be treated as trusted local code.

## Features

- Large application cards and icons
- Drag-and-drop ordering
- Open applications in separate browser tabs
- Start, stop, and status controls for each application. Each action runs its own configured command. If no stop command is supplied, the launcher gracefully terminates the process group/tree it launched, then force-terminates it after a timeout if required.
- Application health/status indicators
- Hidden applications protected by a launcher password
- Dark, midnight, and light themes
- Add, edit, and remove application entries

## Security note

The launcher binds to `127.0.0.1` by default. The password is stored as a salted PBKDF2-HMAC-SHA256 hash. The JSON configuration file itself is not encrypted.

## Logs and runtime files

- `logs/launcher.log` — launcher output (macOS/Linux)
- `logs/launcher-error.log` — launcher errors (Windows)
- `.launcher.pid` — launcher process ID
- `.venv/` — Python virtual environment

## Lifecycle command examples

macOS/Linux:

```bash
Start:  bash run.sh start
Stop:   bash run.sh stop       # optional
Status: bash run.sh status    # optional
```

Windows:

```bat
Start:  run.bat start
Stop:   run.bat stop           rem optional
Status: run.bat status        rem optional
```

A simple foreground process can use a start command such as `python3 app.py`. If the stop command is blank, the launcher tracks the process group created for that command and sends a graceful termination signal before escalating. Commands that deliberately daemonize into a separate session should provide their own stop command or PID-file-aware lifecycle command, because detached descendants cannot reliably be identified as part of the original process group.


## Application lifecycle diagnostics

Lifecycle commands run with the configured application folder as the process working directory (`cwd`). The launcher does not prepend a shell `cd` expression to the command, so folder names containing spaces or quote characters are handled by the operating system. On macOS/Linux, commands are executed using Bash when available; on Windows, the configured command is executed through `cmd.exe`.

Each application has a **Logs** action on its card. It displays the command, application working directory, shell executable, timestamp, exit code, captured standard output/error, and the raw output from start commands. Logs are stored under `runtime/<application-id>.lifecycle.log` and `runtime/<application-id>.log`.
