from flask import Blueprint, request, jsonify, current_app, render_template, send_from_directory
from plugins.plugin_registry import get_plugin_instance
from utils.app_utils import resolve_path, handle_request_files, parse_form
from refresh_task import ManualRefresh, PlaylistRefresh
import json
import os
import logging

logger = logging.getLogger(__name__)
plugin_bp = Blueprint("plugin", __name__)

def _delete_plugin_instance_images(device_config, plugin_instance_obj):
    """Delete all images associated with a plugin instance."""
    # Delete the plugin instance's generated image
    plugin_image_path = os.path.join(device_config.plugin_image_dir, plugin_instance_obj.get_image_path())
    if os.path.exists(plugin_image_path):
        try:
            os.remove(plugin_image_path)
            logger.info(f"Deleted plugin instance image: {plugin_image_path}")
        except Exception as e:
            logger.warning(f"Failed to delete plugin instance image {plugin_image_path}: {e}")

    # Call the plugin's cleanup method to handle plugin-specific resource cleanup
    try:
        plugin_config = device_config.get_plugin(plugin_instance_obj.plugin_id)
        if plugin_config:
            plugin = get_plugin_instance(plugin_config)
            plugin.cleanup(plugin_instance_obj.settings)
    except Exception as e:
        logger.warning(f"Error during plugin cleanup for {plugin_instance_obj.plugin_id}: {e}")

# Removed module-level PLUGINS_DIR - will resolve dynamically in route handlers

@plugin_bp.route('/plugin/<plugin_id>')
def plugin_page(plugin_id):
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    # Find the plugin by id
    plugin_config = device_config.get_plugin(plugin_id)
    if plugin_config:
        try:
            plugin = get_plugin_instance(plugin_config)
            template_params = plugin.generate_settings_template()

            # retrieve plugin instance from the query parameters if updating existing plugin instance
            plugin_instance_name = request.args.get('instance')
            if plugin_instance_name:
                plugin_instance = playlist_manager.find_plugin(plugin_id, plugin_instance_name)
                if not plugin_instance:
                    return jsonify({"error": f"Plugin instance: {plugin_instance_name} does not exist"}), 500

                # add plugin instance settings to the template to prepopulate
                template_params["plugin_settings"] = plugin_instance.settings
                template_params["plugin_instance"] = plugin_instance_name
                template_params["refresh_settings"] = plugin_instance.refresh

            template_params["playlists"] = playlist_manager.get_playlist_names()
        except Exception as e:
            logger.exception("EXCEPTION CAUGHT: " + str(e))
            return jsonify({"error": f"An error occurred: {str(e)}"}), 500
        return render_template('plugin.html', plugin=plugin_config, **template_params)
    else:
        return "Plugin not found", 404

@plugin_bp.route('/images/<plugin_id>/<path:filename>')
def image(plugin_id, filename):
    # Resolve plugins directory dynamically
    plugins_dir = resolve_path("plugins")

    # Construct the full path to the plugin's file
    plugin_dir = os.path.join(plugins_dir, plugin_id)

    # Security check to prevent directory traversal
    safe_path = os.path.abspath(os.path.join(plugin_dir, filename))
    if not safe_path.startswith(os.path.abspath(plugins_dir)):
        return "Invalid path", 403

    # Convert to absolute path for send_from_directory
    abs_plugin_dir = os.path.abspath(plugin_dir)

    # Check if the directory and file exist
    if not os.path.isdir(abs_plugin_dir):
        logger.error(f"Plugin directory not found: {abs_plugin_dir}")
        return "Plugin directory not found", 404

    if not os.path.isfile(safe_path):
        logger.error(f"File not found: {safe_path}")
        return "File not found", 404

    # Serve the file from the plugin directory
    return send_from_directory(abs_plugin_dir, filename)

@plugin_bp.route('/plugin_instance_image/<path:playlist_name>/<path:plugin_id>/<path:instance_name>')
def plugin_instance_image(playlist_name, plugin_id, instance_name):
    """Serve the generated image for a plugin instance."""
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    # Find the plugin instance
    playlist = playlist_manager.get_playlist(playlist_name)
    if not playlist:
        return "Playlist not found", 404

    plugin_instance = playlist.find_plugin(plugin_id, instance_name)
    if not plugin_instance:
        return "Plugin instance not found", 404

    # Get the image path
    image_filename = plugin_instance.get_image_path()
    image_path = os.path.join(device_config.plugin_image_dir, image_filename)

    # Check if the image exists
    if not os.path.exists(image_path):
        # Return a placeholder or 404
        return "Image not yet generated", 404

    # Serve the image
    return send_from_directory(device_config.plugin_image_dir, image_filename)

@plugin_bp.route('/delete_plugin_instance', methods=['POST'])
def delete_plugin_instance():
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    data = request.json
    playlist_name = data.get("playlist_name")
    plugin_id = data.get("plugin_id")
    plugin_instance = data.get("plugin_instance")

    try:
        playlist = playlist_manager.get_playlist(playlist_name)
        if not playlist:
            return jsonify({"success": False, "message": "Playlist not found"}), 400

        # Get the plugin instance to find associated images
        plugin_instance_obj = playlist.find_plugin(plugin_id, plugin_instance)
        if not plugin_instance_obj:
            return jsonify({"success": False, "message": "Plugin instance not found"}), 400

        # Delete associated images before removing from playlist
        _delete_plugin_instance_images(device_config, plugin_instance_obj)

        result = playlist.delete_plugin(plugin_id, plugin_instance)
        if not result:
            return jsonify({"success": False, "message": "Plugin instance not found"}), 400

        # save changes to device config file
        device_config.write_config()

    except Exception as e:
        logger.exception("EXCEPTION CAUGHT: " + str(e))
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Deleted plugin instance."})

@plugin_bp.route('/update_plugin_instance/<string:instance_name>', methods=['PUT'])
def update_plugin_instance(instance_name):
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    try:
        form_data = parse_form(request.form)

        if not instance_name:
            raise RuntimeError("Instance name is required")

        plugin_id = form_data.pop("plugin_id")
        plugin_instance = playlist_manager.find_plugin(plugin_id, instance_name)
        if not plugin_instance:
            return jsonify({"error": f"Plugin instance: {instance_name} does not exist"}), 500

        # Handle refresh settings if provided
        refresh_settings_json = form_data.pop("refresh_settings", None)
        if refresh_settings_json:
            from utils.time_utils import calculate_seconds
            refresh_settings = json.loads(refresh_settings_json)
            refresh_type = refresh_settings.get('refreshType')

            if refresh_type == "interval":
                unit = refresh_settings.get('unit')
                interval = refresh_settings.get('interval')
                if unit and interval:
                    refresh_interval_seconds = calculate_seconds(int(interval), unit)
                    plugin_instance.refresh = {"interval": refresh_interval_seconds}
            elif refresh_type == "scheduled":
                refresh_time = refresh_settings.get('refreshTime')
                if refresh_time:
                    plugin_instance.refresh = {"scheduled": refresh_time}

        # Only update plugin settings if there's actual data (not just refresh settings)
        plugin_settings = form_data
        plugin_settings.update(handle_request_files(request.files, request.form))

        if plugin_settings:  # Only update if there are actual plugin settings
            plugin_instance.settings = plugin_settings

        device_config.write_config()
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500
    return jsonify({"success": True, "message": f"Updated plugin instance {instance_name}."})

@plugin_bp.route('/display_plugin_instance', methods=['POST'])
def display_plugin_instance():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()

    data = request.json
    playlist_name = data.get("playlist_name")
    plugin_id = data.get("plugin_id")
    plugin_instance_name = data.get("plugin_instance")

    try:
        playlist = playlist_manager.get_playlist(playlist_name)
        if not playlist:
            return jsonify({"success": False, "message": f"Playlist {playlist_name} not found"}), 400

        plugin_instance = playlist.find_plugin(plugin_id, plugin_instance_name)
        if not plugin_instance:
            return jsonify({"success": False, "message": f"Plugin instance '{plugin_instance_name}' not found"}), 400

        refresh_task.manual_update(PlaylistRefresh(playlist, plugin_instance, force=True))
    except Exception as e:
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Display updated"}), 200

@plugin_bp.route('/api/plugin_image/<path:filename>')
def get_plugin_image(filename):
    """Serve a single uploaded image file by basename.

    Used by the companion app's Photos screen to render thumbnails for
    every image in an image_upload instance (the legacy
    /plugin_instance_image route only exposes the cached *display*
    image, which doesn't exist until the instance has been rendered).
    Security: we strip the path to its basename so this can never
    escape `device_config.plugin_image_dir`.
    """
    device_config = current_app.config['DEVICE_CONFIG']
    safe_name = os.path.basename(filename)
    full_path = os.path.join(device_config.plugin_image_dir, safe_name)
    if not os.path.isfile(full_path):
        return "Not found", 404
    return send_from_directory(device_config.plugin_image_dir, safe_name)


@plugin_bp.route('/api/remove_image', methods=['POST'])
def remove_image_from_instance():
    """Remove a single uploaded image from an image_upload instance.

    Body: { playlist_name, plugin_instance, image_path }
    plugin_path is the absolute path the Pi stored on disk and is what
    the instance's imageFiles[] setting references.

    If this drops the last image, the instance is *not* auto-deleted —
    the caller can decide whether to delete the empty instance (UI
    typically does, via the existing /delete_plugin_instance handler).
    """
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    data = request.get_json() or {}
    playlist_name = data.get('playlist_name')
    plugin_id = data.get('plugin_id', 'image_upload')
    instance_name = data.get('plugin_instance')
    image_path = data.get('image_path')

    if not playlist_name or not instance_name or not image_path:
        return jsonify({
            "error": "playlist_name, plugin_instance, image_path are required"
        }), 400

    playlist = playlist_manager.get_playlist(playlist_name)
    if not playlist:
        return jsonify({"error": f"Playlist '{playlist_name}' not found"}), 404
    instance = playlist.find_plugin(plugin_id, instance_name)
    if not instance:
        return jsonify({"error": f"Instance '{instance_name}' not found"}), 404

    images = list(instance.settings.get('imageFiles[]', []))
    if image_path not in images:
        return jsonify({"error": "image_path is not part of this instance"}), 404

    images.remove(image_path)
    instance.settings['imageFiles[]'] = images
    # If the cursor was pointing past the (now shorter) list, reset.
    idx = instance.settings.get('image_index', 0)
    if isinstance(idx, int) and idx >= len(images):
        instance.settings['image_index'] = 0

    # Best-effort delete of the file on disk.
    if os.path.isfile(image_path):
        try:
            os.remove(image_path)
            logger.info(f"Removed uploaded image: {image_path}")
        except OSError as e:
            logger.warning(f"Failed to remove {image_path}: {e}")

    device_config.write_config()
    return jsonify({"success": True, "remaining": len(images)})


@plugin_bp.route('/api/update_instance', methods=['POST'])
def api_update_instance():
    """JSON-friendly companion-app variant of /update_plugin_instance.

    Body:
      {
        playlist_name:  str,
        plugin_id:      str,
        plugin_instance: str,           # current instance name
        new_name:       str | None,     # rename (optional)
        plugin_settings: dict | None,   # full replacement settings (optional)
        refresh:        dict | None,    # {interval, unit} or {scheduled: "HH:MM"}
      }

    Unspecified fields are left alone. The existing PUT /update_plugin_instance
    handler is form-only and replaces settings wholesale; this endpoint lets
    the app modify just the bits it knows about without round-tripping every
    setting through form encoding (especially awkward for nested values).
    """
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    data = request.get_json() or {}
    playlist_name = data.get('playlist_name')
    plugin_id = data.get('plugin_id')
    instance_name = data.get('plugin_instance')
    new_name = data.get('new_name')
    new_settings = data.get('plugin_settings')
    refresh = data.get('refresh')

    if not playlist_name or not plugin_id or not instance_name:
        return jsonify({
            "error": "playlist_name, plugin_id, plugin_instance are required"
        }), 400

    playlist = playlist_manager.get_playlist(playlist_name)
    if not playlist:
        return jsonify({"error": f"Playlist '{playlist_name}' not found"}), 404
    instance = playlist.find_plugin(plugin_id, instance_name)
    if not instance:
        return jsonify({"error": f"Instance '{instance_name}' not found"}), 404

    if isinstance(new_name, str) and new_name.strip() and new_name != instance_name:
        candidate = new_name.strip()
        if not all(c.isalnum() or c.isspace() for c in candidate):
            return jsonify({"error": "Instance name can only contain alphanumeric and spaces"}), 400
        if playlist.find_plugin(plugin_id, candidate):
            return jsonify({"error": f"Instance '{candidate}' already exists"}), 400
        instance.name = candidate

    if refresh is not None:
        if not isinstance(refresh, dict):
            return jsonify({"error": "refresh must be an object"}), 400
        if 'interval' in refresh and 'unit' in refresh:
            from utils.time_utils import calculate_seconds
            try:
                seconds = calculate_seconds(int(refresh['interval']), refresh['unit'])
            except (ValueError, TypeError):
                return jsonify({"error": "refresh.interval / refresh.unit invalid"}), 400
            instance.refresh = {"interval": seconds}
        elif 'scheduled' in refresh:
            instance.refresh = {"scheduled": refresh['scheduled']}

    if new_settings is not None:
        if not isinstance(new_settings, dict):
            return jsonify({"error": "plugin_settings must be an object"}), 400
        instance.settings = new_settings

    device_config.write_config()
    return jsonify({"success": True})


@plugin_bp.route('/api/reorder_playlist', methods=['POST'])
def reorder_playlist():
    """Replace a playlist's plugin instance order.

    Body: { playlist_name, order: [ {plugin_id, name}, ... ] }
    """
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    data = request.get_json() or {}
    playlist_name = data.get('playlist_name')
    order = data.get('order') or []

    if not playlist_name or not isinstance(order, list):
        return jsonify({"error": "playlist_name and order list required"}), 400

    playlist = playlist_manager.get_playlist(playlist_name)
    if not playlist:
        return jsonify({"error": f"Playlist '{playlist_name}' not found"}), 404

    keyed = {}
    for p in playlist.plugins:
        keyed[(p.plugin_id, p.name)] = p

    new_list = []
    for entry in order:
        if not isinstance(entry, dict):
            continue
        key = (entry.get('plugin_id'), entry.get('name'))
        if key in keyed:
            new_list.append(keyed.pop(key))
    # Append anything not mentioned in the order — never drop instances
    # silently.
    new_list.extend(keyed.values())
    playlist.plugins = new_list
    # Cursor may now point at a different plugin — reset to start so the
    # next refresh tick re-evaluates rather than jumping mid-list.
    playlist.current_plugin_index = None
    device_config.write_config()

    return jsonify({"success": True, "count": len(new_list)})


@plugin_bp.route('/update_now', methods=['POST'])
def update_now():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    display_manager = current_app.config['DISPLAY_MANAGER']

    try:
        plugin_settings = parse_form(request.form)
        plugin_settings.update(handle_request_files(request.files))
        plugin_id = plugin_settings.pop("plugin_id")

        # Check if refresh task is running
        if refresh_task.running:
            refresh_task.manual_update(ManualRefresh(plugin_id, plugin_settings))
        else:
            # In development mode, directly update the display
            logger.info("Refresh task not running, updating display directly")
            plugin_config = device_config.get_plugin(plugin_id)
            if not plugin_config:
                return jsonify({"error": f"Plugin '{plugin_id}' not found"}), 404

            plugin = get_plugin_instance(plugin_config)
            image = plugin.generate_image(plugin_settings, device_config)
            display_manager.display_image(image, image_settings=plugin_config.get("image_settings", []))

    except Exception as e:
        logger.exception(f"Error in update_now: {str(e)}")
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Display updated"}), 200
