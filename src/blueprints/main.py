from flask import Blueprint, request, jsonify, current_app, render_template, send_file
import json
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


@main_bp.route('/api/display/history')
def api_display_history():
    """List the last N images the panel actually displayed.

    Populated by refresh_task on every successful display update —
    see utils.display_history. Returns newest-first JSON. The
    companion app shows this as a horizontally-scrolling strip
    below the live preview.
    """
    from utils import display_history
    device_config = current_app.config['DEVICE_CONFIG']
    entries = display_history.list_entries(device_config)
    return jsonify({
        "entries": [
            {
                "id":              e["id"],
                "timestamp_ms":    e.get("timestamp_ms"),
                "plugin_id":       e.get("plugin_id"),
                "plugin_instance": e.get("plugin_instance"),
                "playlist":        e.get("playlist"),
                "image_url":       f'/api/display/history/{e["id"]}',
            }
            for e in entries
        ],
    })


@main_bp.route('/api/display/history/<entry_id>')
def api_display_history_image(entry_id):
    """Serve the PNG for one history entry. Basename-sanitised."""
    from utils import display_history
    device_config = current_app.config['DEVICE_CONFIG']
    safe = os.path.basename(entry_id)
    entry = display_history.find_entry(device_config, safe)
    if entry is None:
        return "Not found", 404
    return send_file(entry["image_path"], mimetype='image/png')


@main_bp.route('/api/display/history/<entry_id>/replay', methods=['POST'])
def api_display_history_replay(entry_id):
    """Re-display a historical image immediately. Skips the plugin
    pipeline — sends the saved PNG straight to the existing display
    manager, so plugin_index / next-in-cycle state isn't perturbed.

    Uses the shared display_manager Flask was initialised with
    (app.config['DISPLAY_MANAGER']) rather than instantiating a
    fresh one — re-initialising the e-ink SPI bus on every replay
    is slow + risky on real hardware."""
    from utils import display_history
    device_config = current_app.config['DEVICE_CONFIG']
    display_manager = current_app.config.get('DISPLAY_MANAGER')
    if display_manager is None:
        return jsonify({"error": "display manager not initialised"}), 500
    safe = os.path.basename(entry_id)
    entry = display_history.find_entry(device_config, safe)
    if entry is None:
        return jsonify({"error": "history entry not found"}), 404

    try:
        from PIL import Image
        with Image.open(entry["image_path"]) as img:
            image = img.copy()
        display_manager.display_image(image, image_settings=[])
        return jsonify({
            "success":  True,
            "id":       safe,
            "replayed": entry.get("plugin_instance"),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@main_bp.route('/api/wifi/scan')
def api_wifi_scan():
    """HTTP fast-path for the companion app's Wi-Fi screen.

    The app previously did this over BLE (sendWifi 'scan'), which
    cost a full nmcli rescan PLUS BLE framing overhead — combined
    ~5-7s on a Pi Zero 2 W. Going direct over HTTP cuts the BLE
    framing tax (~400ms) and lets the screen run scan + saved
    fetches truly in parallel.
    """
    from network import wifi as _wifi
    try:
        networks = _wifi.scan()
        return jsonify({
            "networks": [
                {
                    "ssid":     n.ssid,
                    "signal":   n.signal,
                    "security": n.security,
                    "in_use":   n.in_use,
                }
                for n in networks
            ],
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "networks": []}), 500


@main_bp.route('/api/wifi/saved')
def api_wifi_saved():
    """Saved Wi-Fi SSIDs (nmcli profiles, excluding the AP fallback).
    Independent from /api/wifi/scan — saved profiles populate
    instantly while a scan is in flight."""
    from network import wifi as _wifi
    try:
        return jsonify({"ssids": _wifi.list_saved()})
    except Exception as exc:
        return jsonify({"error": str(exc), "ssids": []}), 500


@main_bp.route('/api/wifi/connect', methods=['POST'])
def api_wifi_connect():
    """Connect to an SSID. {ssid, password?}. Same handler the BLE
    bridge calls, exposed for the companion app's Wi-Fi screen."""
    from network import wifi as _wifi
    body = request.get_json(silent=True) or {}
    ssid = body.get('ssid')
    password = body.get('password')
    if not ssid:
        return jsonify({"error": "missing ssid"}), 400
    try:
        status = _wifi.connect(ssid, password=password)
        return jsonify({
            "success": True,
            "wifi": {
                "mode": status.mode,
                "ssid": status.ssid,
                "ip":   status.ip,
            },
        })
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@main_bp.route('/api/wifi/forget', methods=['POST'])
def api_wifi_forget():
    """Delete a saved Wi-Fi profile. {ssid}."""
    from network import wifi as _wifi
    body = request.get_json(silent=True) or {}
    ssid = body.get('ssid')
    if not ssid:
        return jsonify({"error": "missing ssid"}), 400
    try:
        _wifi.forget(ssid)
        return jsonify({"success": True})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@main_bp.route('/api/plugins/catalog')
def api_plugins_catalog():
    """Rich plugin metadata for the companion app's plugin browser.

    Loads each plugin's plugin-info.json (name, description, icon,
    capabilities) and pairs it with whether the plugin is currently
    represented in any playlist. Lets the app render a "browse and
    add" experience richer than the plain list already on
    /api/state.
    """
    device_config = current_app.config['DEVICE_CONFIG']
    plugins_meta = device_config.get_plugins() or []
    used_ids: set[str] = set()
    for pl in device_config.get_playlist_manager().to_dict().get('playlists', []):
        for p in pl.get('plugins', []) or []:
            pid = p.get('plugin_id')
            if pid:
                used_ids.add(pid)

    catalog = []
    for meta in plugins_meta:
        pid = meta.get('id') or meta.get('plugin_id')
        catalog.append({
            "id":               pid,
            "display_name":     meta.get('display_name') or meta.get('name') or pid,
            "description":      meta.get('description'),
            "icon_url":         f'/static/images/plugins/{pid}/icon.png' if pid else None,
            "capabilities":     meta.get('capabilities') or [],
            "requires_api_key": meta.get('requires_api_key', False),
            "configured":       pid in used_ids,
        })
    # Annotate with recent-error count from the in-process ring buffer
    # so the catalog list can show a red dot on plugins that have been
    # failing without the UI doing N follow-up requests.
    try:
        from utils import plugin_errors as _pe
        err_map = _pe.get_all()
        for entry in catalog:
            errs = err_map.get(entry["id"]) or []
            entry["recent_error_count"] = len(errs)
            entry["last_error_at"] = errs[0]["at"] if errs else None
    except Exception:
        pass
    catalog.sort(key=lambda c: (not c["configured"], (c["display_name"] or '').lower()))
    return jsonify({"plugins": catalog})


@main_bp.route('/api/plugins/errors')
def api_plugin_errors_all():
    """Recent render failures across every plugin, newest first per id.

    {"errors": {"weather": [{"at": ..., "type": ..., "message": ...}, ...]}}.
    Used by the companion app's dashboard to surface a "3 plugins have
    been failing" indicator without polling N per-plugin endpoints.
    """
    from utils import plugin_errors
    return jsonify({"errors": plugin_errors.get_all()})


@main_bp.route('/api/plugins/<plugin_id>/errors')
def api_plugin_errors_one(plugin_id):
    """Recent render failures for one plugin id, newest first.

    Errors come from the in-process ring buffer; reset on service
    restart. Each entry is {at: ISO timestamp, type: exception class,
    message: short str}.
    """
    from utils import plugin_errors
    return jsonify({
        "plugin_id": plugin_id,
        "errors": plugin_errors.get(plugin_id),
    })


@main_bp.route('/api/plugins/<plugin_id>/errors', methods=['DELETE'])
def api_plugin_errors_clear(plugin_id):
    """Drop the error buffer for one plugin (after the user has
    acknowledged the failure on the companion app)."""
    from utils import plugin_errors
    plugin_errors.clear(plugin_id)
    return jsonify({"ok": True})


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
        # -c safe.directory=* tells git to trust this repo regardless
        # of its filesystem ownership. inkypi.service runs as root but
        # the working tree typically lives in /home/<user>/, so without
        # this flag modern git refuses with "detected dubious
        # ownership" and the endpoint mis-reports "not a git checkout".
        return subprocess.run(
            ['git', '-c', 'safe.directory=*', '-C', repo_root, *args],
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

        # Best-effort fetch — explicitly catch failures (timeout, no
        # internet, blocked outbound HTTPS, transient DNS) so we can
        # still report the local state from cached refs. Without this
        # catch a Pi without upstream connectivity made the whole
        # endpoint 504 and the app rendered "offline" with nothing
        # else actionable.
        fetch_failed = None
        try:
            _git('fetch', '--quiet', timeout=20)
        except subprocess.TimeoutExpired:
            fetch_failed = "git fetch timed out (no internet?)"
        except Exception as exc:
            fetch_failed = f"git fetch failed: {exc}"

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

        # Detect "git pulled but update.sh never finished its run".
        # update.sh writes LAST_UPDATE_MARKER ($CURRENT_COMMIT) only
        # after every step (apt / pip / unit files / avahi / cli /
        # vendor sync) succeeded. If the file exists and disagrees
        # with HEAD, the working tree is on a newer commit than the
        # last fully-applied update — so apt / service state may be
        # stale even though `git rev-list HEAD..@{u}` says zero.
        # Without this signal the app would (and did, until just now)
        # render "Up to date" even when the user's last update half-
        # completed.
        incomplete = False
        last_applied_short = None
        marker_path = '/var/lib/inkypi/last-updated-commit'
        if os.path.isfile(marker_path):
            try:
                with open(marker_path) as _f:
                    last_applied = _f.read().strip()
                if last_applied and last_applied != head_full:
                    incomplete = True
                    last_applied_short = last_applied[:7]
            except OSError:
                pass

        # Pick up the most recent failure record (if any) and the
        # in-flight lock so the app can render the right state when
        # the user's last apply was interrupted by a phone-sleep,
        # network blip, etc. — those scenarios used to look identical
        # to "everything's fine" on a fresh check.
        last_failure = _read_update_failure()
        running = None
        try:
            with open('/var/lib/inkypi/update-running.json') as _f:
                import json as _json2
                running = _json2.load(_f)
        except (OSError, ValueError):
            running = None

        return jsonify({
            "available":           behind > 0,
            "behind":              behind,
            "current_short":       head,
            "upstream_short":      upstream,
            "changelog":           changelog,
            # Surface fetch-failed-but-cached-refs-used so the app
            # can render an advisory ("Couldn't reach GitHub; showing
            # cached refs") rather than silently returning offline.
            "fetch_warning":       fetch_failed,
            # HEAD doesn't match LAST_UPDATE_MARKER → previous
            # update.sh run was interrupted; system state is out of
            # sync with the code on disk.
            "incomplete_apply":    incomplete,
            "last_applied_short":  last_applied_short,
            # Persistent failure record from the most recent apply, if
            # the user disconnected mid-apply or the run failed but
            # the streaming response was already closed. Cleared by a
            # subsequent successful apply.
            "last_failure":        last_failure,
            # If an update is in flight RIGHT NOW (detached subprocess
            # from a previous request whose client went away), surface
            # it so the app can render "still running" instead of
            # offering an Update button that would race.
            "currently_running":   running,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "git command timed out", "available": False, "behind": 0}), 504
    except Exception as e:
        return jsonify({"error": str(e), "available": False, "behind": 0}), 500


@main_bp.route('/api/plugins/clock/faces')
def api_plugins_clock_faces():
    """Expose the clock plugin's built-in face list to the companion
    app so it can render a face-picker without hardcoding the catalog.

    Returns {"faces": [{name, primary_color, secondary_color, icon_url}]}.
    Icon URLs are absolute server paths (the same /images/<plugin_id>
    route the web UI uses) so the app's <Image.network> can fetch
    them directly.
    """
    from plugins.clock.clock import CLOCK_FACES
    out = []
    for face in CLOCK_FACES:
        icon = face.get('icon') or ''
        # The web UI uses `url_for('plugin.image', plugin_id='clock', filename=face.icon)`
        # which resolves to /images/clock/<filename>. Mirror that here.
        out.append({
            'name':            face['name'],
            'primary_color':   face['primary_color'],
            'secondary_color': face['secondary_color'],
            'icon_url':        f'/images/clock/{icon}',
        })
    return jsonify({"faces": out})


@main_bp.route('/api/updates/ota/releases')
def api_updates_ota_releases():
    """List recent releases from the upstream GitHub repo.

    Used by the companion app's Updates screen to populate a tag
    picker for the OTA flow (an alternative to `git pull`).
    Returns {"releases": [...], "repo": "owner/name"} on success.
    """
    src_dir = os.path.realpath(os.path.dirname(os.path.dirname(__file__)))
    repo_root = os.path.dirname(src_dir)
    from utils import ota as _ota
    try:
        owner_repo = _ota.detect_github_repo(repo_root)
        repo_label = '/'.join(owner_repo) if owner_repo else None
        releases = _ota.list_releases(repo_root, limit=20)
        return jsonify({"releases": releases, "repo": repo_label})
    except Exception as exc:
        current_app.logger.exception("OTA releases listing failed")
        return jsonify({"error": str(exc)}), 502


@main_bp.route('/api/updates/ota/apply', methods=['POST'])
def api_updates_ota_apply():
    """Streaming apply of a release tarball — alternative to the
    existing /api/updates/apply/stream which goes via `git pull`.

    Same NDJSON event shape so the companion app's progress UI can
    consume both endpoints with one parser. Stage names: preflight →
    download → update_sh.

    Body: {"tag": "vX.Y.Z"}.
    """
    from flask import stream_with_context
    body = request.get_json(silent=True) or {}
    tag = (body.get('tag') or '').strip()
    if not tag:
        return jsonify({"error": "Missing 'tag' in request body"}), 400
    src_dir = os.path.realpath(os.path.dirname(os.path.dirname(__file__)))
    repo_root = os.path.dirname(src_dir)

    from utils import ota as _ota
    import json as _json

    def _gen():
        # Schedule the deferred service restart on success — same
        # pattern as /api/updates/apply/stream. Captures the success
        # flag without holding refs to the Flask request after the
        # generator exits.
        success = False
        try:
            for event in _ota.apply_release_streaming(
                    repo_root, tag, defer_restart=True):
                if event.get("event") == "done" and event.get("success"):
                    success = True
                yield _json.dumps(event) + "\n"
        finally:
            if success:
                import subprocess as _sp
                _sp.Popen(
                    ['bash', '-c', 'sleep 8 && systemctl restart inkypi.service'],
                    start_new_session=True,
                )

    return current_app.response_class(
        stream_with_context(_gen()),
        mimetype='application/x-ndjson',
    )


@main_bp.route('/api/updates/last-failure', methods=['DELETE'])
def api_updates_clear_failure():
    """Drop the persisted last-failure record. Used by the companion
    app's "Dismiss" button on the recovered-after-failure banner."""
    _clear_update_failure()
    return jsonify({"ok": True})


@main_bp.route('/api/updates/apply/stream', methods=['POST'])
def api_updates_apply_stream():
    """Streaming version of /api/updates/apply.

    Returns newline-delimited JSON events (Content-Type:
    application/x-ndjson) as the update runs, so the companion app
    can render live progress instead of staring at a spinner for
    8 minutes. The non-streaming endpoint is kept around for older
    app builds.

    Events:
      {"event":"stage_start","stage":"preflight|git_pull|update_sh"}
      {"event":"log","line":"…"}                          # per-line as update.sh prints
      {"event":"stage_complete","stage":"…","step":{...}} # step is same shape as
                                                          # /api/updates/apply
      {"event":"done","success":bool,"stage":"…","error":str?,"steps":[...]}

    Why streaming fixes the SIGINT-at-exit-2 bug: waitress closes a
    request after channel_timeout (120s default) of no I/O on the
    socket. Long apt installs produce no stdout for minutes; the
    socket goes idle; waitress kills it; the subprocess gets killed
    too. Yielding a JSON event per stdout line keeps the socket
    busy and the run finishes cleanly.
    """
    from flask import Response, stream_with_context

    return Response(
        stream_with_context(_apply_update_streaming(request)),
        mimetype='application/x-ndjson',
    )


_FAILURE_RECORD_PATH = '/var/lib/inkypi/last-update-failure.json'


def _write_update_failure(stage: str, error: str, last_step: dict | None = None) -> None:
    """Persist a small JSON record of the most recent apply failure so the
    companion app can surface it AFTER the streaming connection has closed.

    The streaming NDJSON 'done' event with success=false is fine when the
    HTTP connection survives, but transient Wi-Fi drops + Pi-side service
    restarts during the apply mean the app often misses that frame. The
    file is the durable equivalent — readable by /api/updates/check at
    any time, cleared by a subsequent successful apply.
    """
    import json as _json
    import os as _os
    import time
    try:
        _os.makedirs(_os.path.dirname(_FAILURE_RECORD_PATH), exist_ok=True)
        payload = {
            "stage":   stage,
            "error":   error,
            "at":      time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            # Tail of the most diagnostic-rich step (usually update.sh).
            # Truncated so the file never blows past a few KB.
            "cmd":     (last_step or {}).get("cmd"),
            "exit_code": (last_step or {}).get("exit_code"),
            "stdout_tail": ((last_step or {}).get("stdout") or "")[-4096:],
            "stderr_tail": ((last_step or {}).get("stderr") or "")[-4096:],
            "timed_out": (last_step or {}).get("timed_out", False),
        }
        with open(_FAILURE_RECORD_PATH, 'w') as f:
            _json.dump(payload, f)
    except OSError:
        # Best-effort — never let bookkeeping failures mask the real
        # error the user is trying to read.
        pass


def _clear_update_failure() -> None:
    """Drop the failure record after a successful apply so the app stops
    showing the stale 'last failure' banner."""
    import os as _os
    try:
        if _os.path.isfile(_FAILURE_RECORD_PATH):
            _os.remove(_FAILURE_RECORD_PATH)
    except OSError:
        pass


def _read_update_failure() -> dict | None:
    import json as _json
    import os as _os
    if not _os.path.isfile(_FAILURE_RECORD_PATH):
        return None
    try:
        with open(_FAILURE_RECORD_PATH) as f:
            return _json.load(f)
    except (OSError, ValueError):
        return None


def _apply_update_streaming(req):
    """Generator that yields ndjson events as the update progresses.
    Pulled out into a top-level helper so it can be reused / tested
    independently of the Flask Response wrapper."""
    import json as _json
    import os as _os
    import subprocess

    def _emit(payload):
        # On every failure event, persist the failure to disk before
        # yielding it — so even if the network drops mid-flight the
        # companion app sees it on the next /api/updates/check.
        if payload.get("event") == "done" and not payload.get("success", True):
            steps = payload.get("steps") or []
            _write_update_failure(
                stage=payload.get("stage") or "unknown",
                error=payload.get("error") or "Update failed",
                last_step=steps[-1] if steps else None,
            )
        return _json.dumps(payload) + "\n"

    src_dir = _os.path.realpath(_os.path.dirname(_os.path.dirname(__file__)))
    repo_root = _os.path.dirname(src_dir)

    body = req.get_json(silent=True) or {}
    force = bool(body.get('force'))
    discard_local = bool(body.get('discard_local_changes'))

    # Environment for every subprocess: explicit TERM so tput doesn't
    # spew "unknown terminal" warnings into the log. Inherit the rest
    # so PATH / HOME / sudo timestamps work normally.
    base_env = _os.environ.copy()
    base_env.setdefault('TERM', 'dumb')

    def _run_sync(cmd, timeout):
        try:
            r = subprocess.run(
                cmd, cwd=repo_root, capture_output=True, text=True,
                timeout=timeout, env=base_env,
            )
            return {
                "cmd":       ' '.join(cmd),
                "exit_code": r.returncode,
                "stdout":    (r.stdout or '')[-65536:],
                "stderr":    (r.stderr or '')[-65536:],
                "timed_out": False,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "cmd":       ' '.join(cmd),
                "exit_code": -1,
                "stdout":    (exc.stdout.decode('utf-8', 'replace') if isinstance(exc.stdout, bytes) else (exc.stdout or ''))[-32768:],
                "stderr":    (exc.stderr.decode('utf-8', 'replace') if isinstance(exc.stderr, bytes) else (exc.stderr or ''))[-32768:],
                "timed_out": True,
            }

    def _run_streaming(cmd, timeout):
        """Like _run_sync but yields ('log', line) tuples as the
        process prints, returns the final step dict at end. Used for
        update.sh which is the slow phase."""
        import time as _time
        proc = subprocess.Popen(
            cmd, cwd=repo_root, env=base_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=1, text=True,
        )
        start = _time.monotonic()
        collected = []
        timed_out = False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip('\n')
                collected.append(line)
                yield ('log', line)
                if _time.monotonic() - start > timeout:
                    proc.kill()
                    timed_out = True
                    break
        finally:
            try:
                exit_code = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = -1
                timed_out = True
        yield ('step', {
            "cmd":       ' '.join(cmd),
            "exit_code": exit_code,
            "stdout":    '\n'.join(collected[-1000:]),
            "stderr":    '',
            "timed_out": timed_out,
        })

    def _run_streaming_detached(cmd, timeout):
        """Like _run_streaming but the subprocess runs in its own session
        and writes to a log file on disk instead of a pipe. The HTTP
        request tails the log so the user sees live progress; if the
        client disconnects (phone screen sleep is the classic cause)
        the subprocess keeps running to completion and the next
        /api/updates/check sees the result via the LAST_UPDATE_MARKER
        and / or the on-disk failure record.

        Lock file at /var/lib/inkypi/update-running.json marks the
        apply as in-flight so callers can distinguish "still running"
        from "stream dropped, apply failed."
        """
        import time as _time
        log_path = '/var/lib/inkypi/last-update.log'
        lock_path = '/var/lib/inkypi/update-running.json'
        _os.makedirs(_os.path.dirname(log_path), exist_ok=True)
        # Truncate the log so we don't conflate runs.
        log_fd = open(log_path, 'w', buffering=1)
        try:
            proc = subprocess.Popen(
                cmd, cwd=repo_root, env=base_env,
                stdout=log_fd, stderr=subprocess.STDOUT,
                start_new_session=True,  # survive client disconnect
            )
        finally:
            log_fd.close()

        # Drop a marker the check endpoint can read.
        import json as _json
        try:
            with open(lock_path, 'w') as f:
                _json.dump({
                    "pid":        proc.pid,
                    "log_path":   log_path,
                    "started_at": _time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                    "cmd":        ' '.join(cmd),
                }, f)
        except OSError:
            pass

        start = _time.monotonic()
        collected = []
        timed_out = False
        # Tail the log: open separately and read incrementally.
        tail = open(log_path, 'r')
        try:
            while True:
                line = tail.readline()
                if line:
                    line = line.rstrip('\n')
                    collected.append(line)
                    yield ('log', line)
                    continue
                # No new output. Check whether the process exited.
                ret = proc.poll()
                if ret is not None:
                    # Drain any remaining buffered output.
                    for trailing in tail:
                        trailing = trailing.rstrip('\n')
                        collected.append(trailing)
                        yield ('log', trailing)
                    break
                if _time.monotonic() - start > timeout:
                    # update.sh has gone past its budget — kill the
                    # detached process group, not just the leader.
                    try:
                        _os.killpg(_os.getpgid(proc.pid), 15)  # SIGTERM
                    except (ProcessLookupError, PermissionError):
                        pass
                    timed_out = True
                    break
                _time.sleep(0.25)
            exit_code = proc.poll() if proc.poll() is not None else -1
        finally:
            try:
                tail.close()
            except Exception:
                pass
            try:
                _os.remove(lock_path)
            except OSError:
                pass

        yield ('step', {
            "cmd":       ' '.join(cmd),
            "exit_code": exit_code,
            "stdout":    '\n'.join(collected[-1000:]),
            "stderr":    '',
            "timed_out": timed_out,
        })

    steps = []

    # ─── stage: preflight ────────────────────────────────────────────
    yield _emit({"event": "stage_start", "stage": "preflight"})
    diff_check = _run_sync(
        ['git', '-c', 'safe.directory=*', '-C', repo_root, 'status', '--porcelain'],
        timeout=5)
    steps.append(diff_check)
    yield _emit({"event": "stage_complete", "stage": "preflight", "step": diff_check})

    if diff_check["exit_code"] != 0:
        yield _emit({"event": "done", "success": False, "stage": "preflight",
                     "error": "could not read git status", "steps": steps})
        return
    if diff_check["stdout"].strip():
        if not discard_local:
            dirty_lines = diff_check["stdout"].strip().splitlines()[:30]
            yield _emit({
                "event": "done", "success": False, "stage": "preflight",
                "error": "Working tree has uncommitted changes.",
                "dirty_files": dirty_lines, "can_discard": True,
                "steps": steps,
            })
            return
        yield _emit({"event": "log", "line": "Stashing local changes…"})
        stash = _run_sync(
            ['git', '-c', 'safe.directory=*', '-C', repo_root,
             'stash', 'push', '-u', '-m', 'inkypi-pre-update'],
            timeout=15)
        steps.append(stash)
        yield _emit({"event": "stage_complete", "stage": "preflight",
                     "step": stash})
        if stash["exit_code"] != 0:
            yield _emit({
                "event": "done", "success": False, "stage": "preflight",
                "error": "Could not stash local changes — see step output.",
                "steps": steps,
            })
            return

    # ─── stage: git pull ─────────────────────────────────────────────
    yield _emit({"event": "stage_start", "stage": "git_pull"})
    pull = _run_sync(
        ['git', '-c', 'safe.directory=*', '-C', repo_root, 'pull', '--ff-only'],
        timeout=60)
    steps.append(pull)
    if pull["stdout"]:
        for line in pull["stdout"].splitlines():
            yield _emit({"event": "log", "line": line})
    yield _emit({"event": "stage_complete", "stage": "git_pull", "step": pull})
    if pull["exit_code"] != 0:
        yield _emit({"event": "done", "success": False, "stage": "git_pull",
                     "error": "git pull failed — see steps for details",
                     "steps": steps})
        return

    # ─── stage: update.sh — detached, tailed line-by-line ────────────
    yield _emit({"event": "stage_start", "stage": "update_sh"})
    # The previous implementation spawned update.sh as a direct child
    # of the Flask request. When the phone screen sleeps the OS pauses
    # the HTTP stream; waitress eventually drops the connection and
    # SIGTERMs the subprocess, killing the apply mid-run. Detach with
    # start_new_session + redirect output to a log file so the apply
    # survives the client going away. The streaming response then
    # just tail-f's that log file and yields done when update.sh
    # exits. If the client disappears the subprocess keeps running
    # AND the on-disk failure record (written via _emit's hook) is
    # waiting on the next /api/updates/check.
    update_cmd = ['sudo', '-E', 'bash',
                  _os.path.join(repo_root, 'install', 'update.sh'),
                  '--defer-restart']
    if force:
        update_cmd.append('--force')
    final_step = None
    for kind, payload in _run_streaming_detached(update_cmd, timeout=1800):
        if kind == 'log':
            yield _emit({"event": "log", "line": payload})
        else:
            final_step = payload
    if final_step is None:
        final_step = {"cmd": ' '.join(update_cmd), "exit_code": -1,
                      "stdout": '', "stderr": 'no step result',
                      "timed_out": False}
    steps.append(final_step)
    yield _emit({"event": "stage_complete", "stage": "update_sh",
                 "step": final_step})

    success = final_step["exit_code"] == 0 and not final_step["timed_out"]
    if success:
        _clear_update_failure()
        # Spawn a detached child that waits ~8s (long enough for our
        # streaming response to flush + the client's HTTP buffer to
        # drain) and then bounces inkypi.service. start_new_session
        # = True makes it survive systemctl restart inkypi killing
        # our parent. The companion app's auto-await-Pi-back-online
        # loop (in updates_screen) handles the brief downtime.
        try:
            subprocess.Popen(
                ['bash', '-c',
                 'sleep 8 && systemctl restart inkypi.service'],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            # Best-effort: if we can't spawn, the user can restart
            # manually. The done event will still flag success.
            pass
    yield _emit({
        "event":   "done",
        "success": success,
        "stage":   "complete" if success else "update_sh",
        "error":   None if success else "update.sh exited with non-zero status",
        "steps":   steps,
    })


@main_bp.route('/api/updates/apply', methods=['POST'])
def api_updates_apply():
    """Pull + run install/update.sh in-place from the companion app.

    Safety guards:
      - Refuses when the working tree has uncommitted changes — we
        won't silently clobber user edits.
      - Refuses when no upstream branch is tracked (CI dry-runs, etc.).
      - Runs each step with a hard timeout so we can't get wedged.
      - Caps captured output at 64KB so a runaway log doesn't blow
        Flask's response buffer.
      - Returns the full stdout/stderr of each phase so the app can
        surface what happened on failure.

    POSTing with no body is a normal "run it now". Body
    `{"force": true}` bypasses the up-to-date short-circuit in
    update.sh (passes --force through).
    """
    import subprocess

    src_dir = os.path.realpath(os.path.dirname(os.path.dirname(__file__)))
    repo_root = os.path.dirname(src_dir)

    def _run(cmd, timeout, cwd=repo_root):
        try:
            r = subprocess.run(
                cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            )
            return {
                "cmd":         ' '.join(cmd),
                "exit_code":   r.returncode,
                "stdout":      (r.stdout or '')[-65536:],
                "stderr":      (r.stderr or '')[-65536:],
                "timed_out":   False,
            }
        except subprocess.TimeoutExpired as exc:
            return {
                "cmd":         ' '.join(cmd),
                "exit_code":   -1,
                "stdout":      (exc.stdout or b'')[-32768:].decode('utf-8', errors='replace') if exc.stdout else '',
                "stderr":      (exc.stderr or b'')[-32768:].decode('utf-8', errors='replace') if exc.stderr else '',
                "timed_out":   True,
            }

    body = request.get_json(silent=True) or {}
    force = bool(body.get('force'))
    discard_local = bool(body.get('discard_local_changes'))

    # Refuse if the working tree is dirty — overlaying a `git pull` over
    # local edits is the fastest way to silently lose work.
    diff_check = _run(
        ['git', '-c', 'safe.directory=*', '-C', repo_root, 'status', '--porcelain'],
        timeout=5)
    if diff_check["exit_code"] != 0:
        return jsonify({"success": False, "stage": "preflight", "steps": [diff_check],
                        "error": "could not read git status"}), 500
    if diff_check["stdout"].strip():
        if not discard_local:
            # Return the dirty file list so the app can show it AND
            # offer "Discard and retry". Truncate to 30 lines so a
            # runaway never-gitignored directory doesn't blow the
            # response size.
            dirty_lines = diff_check["stdout"].strip().splitlines()[:30]
            return jsonify({
                "success":      False,
                "stage":        "preflight",
                "error":        "Working tree has uncommitted changes.",
                "dirty_files":  dirty_lines,
                "steps":        [diff_check],
                # Tell the app it can retry with this set so the user
                # gets an offer button instead of having to SSH.
                "can_discard":  True,
            }), 409
        # User explicitly asked us to nuke local changes — stash
        # everything (including untracked) so the work isn't gone
        # forever, just out of the way for the pull. We don't auto-pop
        # afterwards because pop-with-conflicts is a worse experience
        # than "your stash is on the Pi, run `git stash pop` to get it
        # back".
        stash = _run(
            ['git', '-c', 'safe.directory=*', '-C', repo_root,
             'stash', 'push', '-u', '-m', 'inkypi-pre-update'],
            timeout=15)
        if stash["exit_code"] != 0:
            return jsonify({
                "success": False,
                "stage":   "preflight",
                "error":   "Could not stash local changes — see step output.",
                "steps":   [diff_check, stash],
            }), 500

    # Pull. If this fails (no network, conflicts, …) abort before
    # touching update.sh.
    pull = _run(
        ['git', '-c', 'safe.directory=*', '-C', repo_root, 'pull', '--ff-only'],
        timeout=60)
    if pull["exit_code"] != 0:
        return jsonify({"success": False, "stage": "git_pull",
                        "steps": [diff_check, pull],
                        "error": "git pull failed — see steps for details"}), 500

    # Run update.sh. Has its own already-up-to-date short-circuit
    # unless we pass --force.
    update_cmd = ['sudo', 'bash', os.path.join(repo_root, 'install', 'update.sh')]
    if force:
        update_cmd.append('--force')
    upd = _run(update_cmd, timeout=900)  # 15 min — apt/pip can be slow

    steps = [diff_check, pull, upd]
    success = upd["exit_code"] == 0 and not upd["timed_out"]
    return jsonify({
        "success": success,
        "stage":   "update_sh" if not success else "complete",
        "steps":   steps,
        "error":   None if success else "update.sh exited with non-zero status",
    }), (200 if success else 500)


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

    def _extra_adv_status():
        """Read what the BLE service most recently wrote about the
        secondary advertisement. None when no status file exists yet
        (BLE service hasn't started, or is on an old build)."""
        try:
            with open('/run/inkypi/extra-adv.status') as f:
                payload = json.load(f)
            return {
                "state":    payload.get("state", "unknown"),
                "mfg_data": payload.get("mfg_data"),
            }
        except (OSError, json.JSONDecodeError):
            return {"state": "unknown", "mfg_data": None}

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
        # Tells "is the IP-in-adv fast path actually advertising?"
        # without needing journalctl access.
        "ble_extra_adv":  _extra_adv_status(),
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


@main_bp.route('/api/system/cleanup', methods=['POST'])
def api_system_cleanup():
    """Reclaim disk space by clearing rebuildable runtime caches.

    Deletes everything under:
      • static/images/history/  — the rolling display-history snapshots
      • static/images/plugins/  — cached per-instance plugin renders

    All entries are rebuildable — the next refresh writes a fresh
    plugin render, the next display update writes a new history
    entry. We never touch static/images/saved/ (user-uploaded
    photos) or device.json.

    Returns the byte count reclaimed + the number of files removed
    per category so the app can show "Reclaimed 42 MB across 87
    files".
    """
    import shutil

    src_root = os.path.realpath(
        os.path.join(os.path.dirname(os.path.dirname(__file__)),
                     'static', 'images'))

    def _wipe_dir(path):
        if not os.path.isdir(path):
            return 0, 0
        bytes_freed = 0
        files_removed = 0
        for name in os.listdir(path):
            full = os.path.join(path, name)
            try:
                if os.path.isfile(full):
                    bytes_freed += os.path.getsize(full)
                    os.remove(full)
                    files_removed += 1
                elif os.path.isdir(full):
                    # Subdirs in plugins/ — total their size then nuke.
                    for root, _, files in os.walk(full):
                        for f in files:
                            try:
                                bytes_freed += os.path.getsize(
                                    os.path.join(root, f))
                                files_removed += 1
                            except OSError:
                                pass
                    shutil.rmtree(full, ignore_errors=True)
            except OSError:
                pass
        return bytes_freed, files_removed

    history_bytes, history_files = _wipe_dir(os.path.join(src_root, 'history'))
    plugins_bytes, plugins_files = _wipe_dir(os.path.join(src_root, 'plugins'))

    total_bytes = history_bytes + plugins_bytes
    total_files = history_files + plugins_files

    return jsonify({
        "success": True,
        "bytes_reclaimed": total_bytes,
        "files_removed":   total_files,
        "breakdown": {
            "history": {"bytes": history_bytes, "files": history_files},
            "plugins": {"bytes": plugins_bytes, "files": plugins_files},
        },
    })


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


_INFO_CACHE = {"at": 0.0, "payload": None}
_INFO_CACHE_TTL = 5.0  # seconds — small window since wifi.ip changes infrequently


@main_bp.route('/api/info')
def api_info():
    """HTTP mirror of the BLE INFO characteristic.

    Cached for ~5 seconds: the companion app probes this endpoint
    *and* reads it for the dashboard status card, so back-to-back
    calls would otherwise both pay the wifi.current_status() cost
    (nmcli shell-out, ~150-300ms on a Pi Zero 2 W). The TTL is short
    enough that meaningful state changes (Wi-Fi joined / dropped)
    surface within a few seconds.
    """
    import time
    import socket as _socket
    from network import wifi as _wifi

    now = time.time()
    if _INFO_CACHE["payload"] is not None and (now - _INFO_CACHE["at"]) < _INFO_CACHE_TTL:
        return jsonify(_INFO_CACHE["payload"])

    device_config = current_app.config['DEVICE_CONFIG']
    cfg = device_config.get_config()

    try:
        status = _wifi.current_status()
    except Exception:
        status = None

    hotspot_cfg = cfg.get("hotspot") or {}

    payload = {
        "name":    cfg.get("name") or _socket.gethostname(),
        "version": cfg.get("version", "1.0.0"),
        "wifi": {
            "mode": status.mode if status else "offline",
            "ssid": status.ssid if status else None,
            "ip":   status.ip if status else None,
        },
        "ap": {
            "ssid":     hotspot_cfg.get("ssid"),
            "password": hotspot_cfg.get("password"),
            "ip":       hotspot_cfg.get("gateway", "192.168.4.1"),
        },
        "display": {
            "resolution":             cfg.get("resolution"),
            "current_plugin":         cfg.get("refresh_info", {}).get("plugin_id"),
            "last_refresh":           cfg.get("refresh_info", {}).get("refresh_time"),
            "cycle_interval_seconds": cfg.get("plugin_cycle_interval_seconds"),
        },
        # Trivially true — if the caller hit this endpoint, Flask is up.
        "flask_reachable": True,
    }
    _INFO_CACHE["at"] = now
    _INFO_CACHE["payload"] = payload
    return jsonify(payload)


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