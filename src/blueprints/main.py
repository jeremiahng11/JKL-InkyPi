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