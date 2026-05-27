from flask import Blueprint, request, jsonify, current_app, render_template, send_file
import os
from datetime import datetime

main_bp = Blueprint("main", __name__)

@main_bp.route('/')
def main_page():
    device_config = current_app.config['DEVICE_CONFIG']
    return render_template('inky.html', config=device_config.get_config(), plugins=device_config.get_plugins())

@main_bp.route('/api/current_image')
def get_current_image():
    """Serve current_image.png with conditional request support (If-Modified-Since)."""
    image_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'images', 'current_image.png')
    
    if not os.path.exists(image_path):
        return jsonify({"error": "Image not found"}), 404
    
    # Get the file's last modified time (truncate to seconds to match HTTP header precision)
    file_mtime = int(os.path.getmtime(image_path))
    last_modified = datetime.fromtimestamp(file_mtime)
    
    # Check If-Modified-Since header
    if_modified_since = request.headers.get('If-Modified-Since')
    if if_modified_since:
        try:
            # Parse the If-Modified-Since header
            client_mtime = datetime.strptime(if_modified_since, '%a, %d %b %Y %H:%M:%S %Z')
            client_mtime_seconds = int(client_mtime.timestamp())
            
            # Compare (both now in seconds, no sub-second precision)
            if file_mtime <= client_mtime_seconds:
                return '', 304
        except (ValueError, AttributeError):
            pass
    
    # Send the file with Last-Modified header
    response = send_file(image_path, mimetype='image/png')
    response.headers['Last-Modified'] = last_modified.strftime('%a, %d %b %Y %H:%M:%S GMT')
    response.headers['Cache-Control'] = 'no-cache'
    return response


@main_bp.route('/api/plugin_order', methods=['POST'])
def save_plugin_order():
    """Save the custom plugin order."""
    device_config = current_app.config['DEVICE_CONFIG']

    data = request.get_json() or {}
    order = data.get('order', [])

    if not isinstance(order, list):
        return jsonify({"error": "Order must be a list"}), 400

    device_config.set_plugin_order(order)

    return jsonify({"success": True})


@main_bp.route('/api/updates/check')
def api_updates_check():
    """Probe the git working tree for available updates.

    Runs `git fetch` against the configured remote (no merge), then
    counts commits the local HEAD is behind upstream by and returns
    them as a short changelog. Read-only — does not apply anything.

    The companion app polls this from its Updates screen so users can
    see "you're 3 commits behind" without SSHing in.

    Returns {available: bool, behind: int, current_short, upstream_short,
             changelog: [str], error: str?}.
    """
    import subprocess

    # The Flask process runs from `/usr/local/inkypi/src` which is a
    # symlink to the actual source checkout. Resolve to the real path
    # so we run git from inside the cloned working tree.
    src_dir = os.path.realpath(os.path.dirname(os.path.dirname(__file__)))
    repo_root = os.path.dirname(src_dir)

    def _git(*args, timeout=15):
        return subprocess.run(
            ['git', '-C', repo_root, *args],
            capture_output=True, text=True, timeout=timeout,
        )

    try:
        # If this isn't a git checkout at all, return a clear "unknown"
        # state rather than a 500.
        check = _git('rev-parse', '--is-inside-work-tree', timeout=3)
        if check.returncode != 0:
            return jsonify({
                "available": False,
                "behind": 0,
                "error": "Pi source is not a git checkout — update check not supported",
            })

        # Best-effort fetch; ignore failure (offline, auth, etc.) and
        # fall back to whatever cached refs we already have.
        _git('fetch', '--quiet', timeout=20)

        head = _git('rev-parse', '--short', 'HEAD').stdout.strip()
        head_full = _git('rev-parse', 'HEAD').stdout.strip()
        upstream_check = _git('rev-parse', '--symbolic-full-name', '@{u}', timeout=3)
        if upstream_check.returncode != 0:
            return jsonify({
                "available": False,
                "behind": 0,
                "current_short": head,
                "error": "No upstream branch is tracked — can't check for updates",
            })

        upstream = _git('rev-parse', '--short', '@{u}').stdout.strip()
        upstream_full = _git('rev-parse', '@{u}').stdout.strip()
        behind_out = _git('rev-list', '--count', f'{head_full}..{upstream_full}').stdout.strip()
        behind = int(behind_out or '0')

        changelog = []
        if behind > 0:
            log = _git(
                'log', '--oneline', '--no-decorate', '-n', '20',
                f'{head_full}..{upstream_full}',
            ).stdout.strip()
            changelog = [line for line in log.splitlines() if line]

        return jsonify({
            "available": behind > 0,
            "behind": behind,
            "current_short": head,
            "upstream_short": upstream,
            "changelog": changelog,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "git command timed out", "available": False, "behind": 0}), 504
    except Exception as e:
        return jsonify({"error": str(e), "available": False, "behind": 0}), 500


@main_bp.route('/api/about')
def api_about():
    """Pi-side identity + health snapshot for the companion app's
    About screen. Reads cheap signals (no shell-outs for things psutil
    can answer), and uses systemctl is-active for service health.
    """
    import platform
    import subprocess

    def _service_state(name):
        try:
            r = subprocess.run(
                ['systemctl', 'is-active', name],
                capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip() or 'unknown'
        except Exception:
            return 'unknown'

    device_config = current_app.config['DEVICE_CONFIG']
    cfg = device_config.get_config()

    return jsonify({
        "hostname":       platform.node(),
        "os":             platform.platform(),
        "python":         platform.python_version(),
        "app_name":       "InkyPi (JKL fork)",
        "app_version":    cfg.get('version', '1.0.0'),
        "display_type":   cfg.get('display_type'),
        "resolution":     cfg.get('resolution'),
        "services": {
            "inkypi":       _service_state('inkypi'),
            "inkypi-ble":   _service_state('inkypi-ble'),
            "inkypi-netd":  _service_state('inkypi-netd'),
            "bluetooth":    _service_state('bluetooth'),
        },
    })


@main_bp.route('/api/backup')
def api_backup():
    """Full device.json snapshot for the companion app's backup feature.

    Returns everything in the on-disk config (excluding nothing — backups
    are useless if they don't restore exactly). Hotspot credentials and
    refresh history are included because the user explicitly asked for a
    backup; API keys live in the .env file and are NOT part of this
    payload.
    """
    device_config = current_app.config['DEVICE_CONFIG']
    payload = {
        "version": 1,
        "kind": "inkypi-device-config",
        "config": device_config.get_config(),
    }
    return jsonify(payload)


@main_bp.route('/api/restore', methods=['POST'])
def api_restore():
    """Replace device.json with the posted backup payload.

    Body: {kind: "inkypi-device-config", version: 1, config: {...}}

    Validates the envelope, refuses obvious garbage (missing resolution
    or playlist_config keys), writes the file atomically via the existing
    Config.write_config path, and reloads the in-memory playlist /
    refresh state so the running service uses the new values immediately
    — no restart needed for most cases.
    """
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config.get('REFRESH_TASK')

    payload = request.get_json(silent=True) or {}
    if payload.get('kind') != 'inkypi-device-config':
        return jsonify({"error": "Backup envelope is missing or invalid"}), 400
    cfg = payload.get('config')
    if not isinstance(cfg, dict):
        return jsonify({"error": "Backup 'config' must be an object"}), 400
    if 'resolution' not in cfg or 'playlist_config' not in cfg:
        return jsonify({"error": "Backup is missing required fields"}), 400

    # Swap the in-memory config dict, persist, and rebuild the cached
    # objects derived from it (playlist manager + refresh info).
    device_config.config = cfg
    device_config.write_config()
    device_config.playlist_manager = device_config.load_playlist_manager()
    device_config.refresh_info = device_config.load_refresh_info()

    # Nudge the refresh task so the next tick re-reads the new state.
    if refresh_task is not None:
        try:
            refresh_task.signal_config_change()
        except Exception:
            pass

    return jsonify({"success": True})


@main_bp.route('/api/system_stats')
def get_system_stats():
    """Lightweight system snapshot for the companion app's dashboard card.

    Returns CPU %, memory %, disk %, 1/5/15 min load averages, and the
    Pi's uptime in seconds. Cheap enough to poll every few seconds.
    """
    try:
        import psutil
        load = os.getloadavg()
        boot_time = psutil.boot_time()
        from time import time as _time
        return jsonify({
            "cpu_percent":    psutil.cpu_percent(interval=0),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent":   psutil.disk_usage('/').percent,
            "load_avg":       list(load),
            "uptime_seconds": int(_time() - boot_time),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@main_bp.route('/api/state')
def get_state():
    """JSON snapshot of device state for the companion app's HTTP fast path.

    The companion app uses BLE for control commands when only Bluetooth is
    available, but switches to HTTP for everything when both phone and Pi
    are on the same network — BLE on the Pi Zero 2 W is fragile and slow.
    This endpoint exposes the read-only subset the app needs (plugins,
    playlists, settings, refresh state) without leaking secrets or
    duplicating the entire device.json schema.
    """
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()
    refresh_info = device_config.get_refresh_info()
    plugins = device_config.get_plugins()
    cfg = device_config.get_config()

    return jsonify({
        "name":          cfg.get("name"),
        "version":       cfg.get("version", "1.0.0"),
        "resolution":    cfg.get("resolution"),
        "orientation":   cfg.get("orientation"),
        "display_type":  cfg.get("display_type"),
        "timezone":      cfg.get("timezone"),
        "time_format":   cfg.get("time_format"),
        "plugin_cycle_interval_seconds": cfg.get("plugin_cycle_interval_seconds"),
        "image_settings":  cfg.get("image_settings", {}),
        "inverted_image":  cfg.get("inverted_image"),
        "log_system_stats": cfg.get("log_system_stats"),
        "plugins":        plugins,
        "plugin_order":   cfg.get("plugin_order", []),
        "playlist_config": playlist_manager.to_dict(),
        "refresh_info":   refresh_info.to_dict(),
    })