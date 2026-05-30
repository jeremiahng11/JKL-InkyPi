from flask import Blueprint, request, jsonify, current_app, render_template, Response
from utils.time_utils import calculate_seconds
from datetime import datetime, timedelta
import json
import os
import pytz
import logging
import io
import time

# Try to import cysystemd for journal reading (Linux only)
try:
    from cysystemd.reader import JournalReader, JournalOpenMode, Rule
    JOURNAL_AVAILABLE = True
except ImportError:
    JOURNAL_AVAILABLE = False
    # Define dummy classes for when cysystemd is not available
    class JournalOpenMode:
        SYSTEM = None
    class Rule:
        pass
    class JournalReader:
        def __init__(self, *args, **kwargs):
            pass


logger = logging.getLogger(__name__)
settings_bp = Blueprint("settings", __name__)

@settings_bp.route('/settings')
def settings_page():
    device_config = current_app.config['DEVICE_CONFIG']
    timezones = sorted(pytz.all_timezones_set)
    return render_template('settings.html', device_settings=device_config.get_config(), timezones = timezones)

@settings_bp.route('/save_settings', methods=['POST'])
def save_settings():
    device_config = current_app.config['DEVICE_CONFIG']

    try:
        form_data = request.form.to_dict()

        unit, interval, time_format = form_data.get('unit'), form_data.get("interval"), form_data.get("timeFormat")
        if not unit or unit not in ["minute", "hour"]:
            return jsonify({"error": "Plugin cycle interval unit is required"}), 400
        if not interval or not interval.isnumeric():
            return jsonify({"error": "Refresh interval is required"}), 400
        if not form_data.get("timezoneName"):
            return jsonify({"error": "Time Zone is required"}), 400
        if not time_format or time_format not in ["12h", "24h"]:
            return jsonify({"error": "Time format is required"}), 400
        previous_interval_seconds = device_config.get_config("plugin_cycle_interval_seconds")
        plugin_cycle_interval_seconds = calculate_seconds(int(interval), unit)
        if plugin_cycle_interval_seconds > 86400 or plugin_cycle_interval_seconds <= 0:
            return jsonify({"error": "Plugin cycle interval must be less than 24 hours"}), 400

        settings = {
            "name": form_data.get("deviceName"),
            "orientation": form_data.get("orientation"),
            "inverted_image": form_data.get("invertImage"),
            "log_system_stats": form_data.get("logSystemStats"),
            "timezone": form_data.get("timezoneName"),
            "time_format": form_data.get("timeFormat"),
            "plugin_cycle_interval_seconds": plugin_cycle_interval_seconds,
            "image_settings": {
                "saturation": float(form_data.get("saturation", "1.0")),
                "brightness": float(form_data.get("brightness", "1.0")),
                "sharpness": float(form_data.get("sharpness", "1.0")),
                "contrast": float(form_data.get("contrast", "1.0"))
            }
        }
        if "inky_saturation" in form_data:
            settings["image_settings"]["inky_saturation"] = float(form_data.get("inky_saturation", "0.5"))
        device_config.update_config(settings)

        if plugin_cycle_interval_seconds != previous_interval_seconds:
            # wake the background thread up to signal interval config change
            refresh_task = current_app.config['REFRESH_TASK']
            refresh_task.signal_config_change()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    return jsonify({"success": True, "message": "Saved settings."})

@settings_bp.route('/shutdown', methods=['POST'])
def shutdown():
    data = request.get_json() or {}
    if data.get("reboot"):
        logger.info("Reboot requested")
        os.system("sudo reboot")
    else:
        logger.info("Shutdown requested")
        os.system("sudo shutdown -h now")
    return jsonify({"success": True})

@settings_bp.route('/download-logs')
def download_logs():
    try:
        buffer = io.StringIO()

        # Get 'hours' from query parameters, default to 2 if not provided or invalid
        hours_str = request.args.get('hours', '2')
        try:
            hours = int(hours_str)
        except ValueError:
            hours = 2
        since = datetime.now() - timedelta(hours=hours)

        # Service selector — whitelist so we can't be tricked into
        # tailing arbitrary systemd units (sshd, etc.). Default stays
        # inkypi.service for backwards compat with the web UI.
        allowed_units = {
            "inkypi.service",
            "inkypi-ble.service",
            "inkypi-netd.service",
            "bluetooth.service",
        }
        service = request.args.get('service', 'inkypi.service')
        if not service.endswith('.service'):
            service = f"{service}.service"
        if service not in allowed_units:
            service = 'inkypi.service'

        if not JOURNAL_AVAILABLE:
            # Return a message when running in development mode without systemd
            buffer.write(f"Log download not available in development mode (cysystemd not installed).\n")
            buffer.write(f"Logs would normally show {service} entries from the last {hours} hours.\n")
            buffer.write(f"\nTo see Flask development logs, check your terminal output.\n")
        else:
            reader = JournalReader()
            reader.open(JournalOpenMode.SYSTEM)
            reader.add_filter(Rule("_SYSTEMD_UNIT", service))
            reader.seek_realtime_usec(int(since.timestamp() * 1_000_000))

            for record in reader:
                try:
                    ts = datetime.fromtimestamp(record.get_realtime_usec() / 1_000_000)
                    formatted_ts = ts.strftime("%b %d %H:%M:%S")
                except Exception:
                    formatted_ts = "??? ?? ??:??:??"

                data = record.data
                hostname = data.get("_HOSTNAME", "unknown-host")
                identifier = data.get("SYSLOG_IDENTIFIER") or data.get("_COMM", "?")
                pid = data.get("_PID", "?")
                msg = data.get("MESSAGE", "").rstrip()

                # Format the log entry similar to the journalctl default output
                buffer.write(f"{formatted_ts} {hostname} {identifier}[{pid}]: {msg}\n")

        buffer.seek(0)
        # Add date and time to the filename
        now_str = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"inkypi_{now_str}.log"
        return Response(
            buffer.read(),
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return Response(f"Error reading logs: {e}", status=500, mimetype="text/plain")


# Shared with /download-logs — keep one source of truth.
_LOG_ALLOWED_UNITS = {
    "inkypi.service",
    "inkypi-ble.service",
    "inkypi-netd.service",
    "bluetooth.service",
}


def _normalize_log_service(raw: str) -> str:
    """Normalize ?service= query param and fall back to inkypi if unknown.

    Whitelists units so we can't be tricked into tailing arbitrary
    systemd journals (sshd, kernel ring buffer, etc.).
    """
    svc = raw or "inkypi.service"
    if not svc.endswith('.service'):
        svc = f"{svc}.service"
    if svc not in _LOG_ALLOWED_UNITS:
        svc = "inkypi.service"
    return svc


@settings_bp.route('/api/logs/stream')
def stream_logs():
    """Server-Sent Events stream of journal entries for a service.

    Yields a short backfill (last few minutes) so the UI shows recent
    context immediately, then live-tails new entries as they arrive.
    Sends a ": keepalive" comment every ~15s when idle so intermediary
    proxies / phone network state don't time the connection out.

    Each data event is JSON: {ts, ident, pid, msg}. The companion
    app's live log view parses these directly without a second decode
    of the journal envelope.
    """
    if not JOURNAL_AVAILABLE:
        # Surface the dev-mode case in a stream-shaped reply so the
        # companion can render "log streaming unavailable" without
        # special-casing the HTTP status.
        def _err_gen():
            yield 'data: {"error":"journal unavailable (dev mode)"}\n\n'
        return Response(_err_gen(), mimetype="text/event-stream")

    service = _normalize_log_service(request.args.get('service', ''))
    try:
        backfill_minutes = max(0, min(60, int(request.args.get('backfill_minutes', '5'))))
    except ValueError:
        backfill_minutes = 5

    def _format(record):
        try:
            ts_us = record.get_realtime_usec()
            ts = datetime.fromtimestamp(ts_us / 1_000_000).isoformat()
        except Exception:
            ts = ""
        data = record.data
        payload = {
            "ts":    ts,
            "ident": data.get("SYSLOG_IDENTIFIER") or data.get("_COMM", "?"),
            "pid":   data.get("_PID"),
            "msg":   (data.get("MESSAGE") or "").rstrip(),
        }
        return f"data: {json.dumps(payload)}\n\n"

    def gen():
        reader = JournalReader()
        reader.open(JournalOpenMode.SYSTEM)
        reader.add_filter(Rule("_SYSTEMD_UNIT", service))
        since = datetime.now() - timedelta(minutes=backfill_minutes)
        reader.seek_realtime_usec(int(since.timestamp() * 1_000_000))

        # First flush — initial "I'm connected" marker so the client
        # can flip its UI out of the "Connecting…" state even if the
        # backfill window is empty.
        yield 'data: {"event":"open","service":"' + service + '"}\n\n'

        last_keepalive = time.monotonic()
        while True:
            emitted = False
            try:
                for record in reader:
                    yield _format(record)
                    emitted = True
                    last_keepalive = time.monotonic()
            except Exception:
                # If the journal handle goes sideways, surface it as a
                # one-shot error event and exit the generator. Client
                # reconnects to recover.
                logger.exception("journal tail failed")
                yield 'data: {"error":"journal tail failed"}\n\n'
                return
            if not emitted:
                if time.monotonic() - last_keepalive > 15:
                    yield ": keepalive\n\n"
                    last_keepalive = time.monotonic()
                time.sleep(1.0)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control":      "no-cache",
            # Defense in depth — Nginx wouldn't be in front of us in
            # the field but in case someone proxies this through one.
            "X-Accel-Buffering":  "no",
            # waitress chunked transfer is on by default; this just
            # documents intent.
            "Transfer-Encoding":  "chunked",
        },
    )

