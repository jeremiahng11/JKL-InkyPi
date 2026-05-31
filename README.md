# InkyPi 

<img src="./docs/images/inky_clock.jpg" />


## About InkyPi 
InkyPi is an open-source, customizable E-Ink display powered by a Raspberry Pi. Designed for simplicity and flexibility, it allows you to effortlessly display the content you care about, with a simple web interface that makes setup and configuration effortless.

**Features**:
- Natural paper-like aethetic: crisp, minimalist visuals that are easy on the eyes, with no glare or backlight
- Web Interface allows you to update and configure the display from any device on your network
- **Bluetooth control via the InkyPi Companion app** — set up Wi-Fi, swap plugins, upload images, and tweak settings from your phone with no web browser needed
- **Wi-Fi hotspot fallback** — when InkyPi has no upstream network it broadcasts its own access point so you're never locked out, even on a fresh install
- **Self-healing renders** — plugin failures are retried once after a 30 s backoff before the cycle moves on; recent failures are recorded in a per-plugin ring buffer surfaced to the companion app
- **Live log streaming** — `/api/logs/stream` (Server-Sent Events) tails the systemd journal of any whitelisted service for diagnostics from the phone
- **Streaming updates** — the companion app drives `update.sh` over a streaming NDJSON endpoint with deferred service restart, auto-snapshot, and incomplete-apply detection
- Minimize distractions: no LEDS, noise, or notifications, just the content you care about
- Easy installation and configuration, perfect for beginners and makers alike
- Open source project allowing you to modify, customize, and create your own plugins
- Set up scheduled playlists to display different plugins at designated times

**Plugins**:

- Image Upload: Upload and display any image from your browser
- Daily Newspaper/Comic: Show daily comics and front pages of major newspapers from around the world
- Clock: Customizable clock faces for displaying time
- AI Image/Text: Generate images and dynamic text from prompts using OpenAI's models
- Weather: Display current weather conditions and multi-day forecasts with a customizable layout
- Calendar: Visualize your calendar from Google, Outlook, or Apple Calendar with customizable layouts

And additional plugins coming soon! For documentation on building custom plugins, see [Building InkyPi Plugins](./docs/building_plugins.md).

## Mobile Companion App

InkyPi ships with a Bluetooth Low Energy interface so you can control the
display from a Flutter-based mobile app instead of (or alongside) the web
UI. Useful when:

- You're setting up a brand-new Pi and don't have a keyboard or known
  Wi-Fi network on hand
- Your home Wi-Fi is down but you still want to swap what's on the screen
- You just prefer a tap-to-do-everything experience over visiting a URL

Companion app source + build instructions:
**[InkyPi-Companion](https://github.com/jeremiahng11/InkyPi-Companion)** (Android + iOS).

### What you can do from the app

The companion app uses a four-tab bottom nav: **Dashboard / Photos / Plugins / Settings**.

| Screen | Capability |
|--------|------------|
| **Find InkyPi** | Single list of every reachable Pi — Wi-Fi targets (mDNS + cached IP + IP from BLE-advertisement manufacturer data, deduplicated) on top, Bluetooth scan results below. Wi-Fi is auto-preferred when reachable. First-launch users see a 3-page onboarding flow first (welcome → Bluetooth permission → Wi-Fi explainer). |
| **Dashboard** | Live status: Wi-Fi mode + SSID + IP, e-ink resolution, "Now showing X · 5m ago", "Next refresh in ~Nm" countdown, multi-device switcher with per-device reachability dots, Pi health badge in the app bar. Inline preview auto-refreshes whenever the Pi reports a new render. Skip / force-refresh buttons show a spinner while the request is in flight. CPU / memory / disk / load stats card auto-pauses when you're on a different tab. A "Pi restarting" pane replaces the generic "Not connected" view during update / reboot windows; a local notification fires when the Pi is reachable again. |
| **Photos** | Gallery of every uploaded image flattened across instances. Tap to view full-size, display now, or delete. **Long-press to enter selection mode** → bulk delete picks per-instance (drops the whole instance when all of its images are selected, removes individuals otherwise). FAB opens the Upload flow. |
| **Plugins** | Browse configured plugins, switch the active one, tap an instance to push it to the display, or edit any instance (name, refresh interval, settings). For clock instances the editor surfaces a native **face picker** (Gradient / Digital / Divided / Word) with primary / secondary color inputs. The catalog page has a search box and shows a **red error pill** on any plugin that's been failing recently — tap it for the per-plugin error history. |
| **Upload image** | Pick from gallery / camera / URL. Transport picker shows whether you're going over Wi-Fi (fast) or Bluetooth (slow); cellular-only state surfaces a "join the same Wi-Fi for ~1000× faster uploads" hint. Optional **Save to playlist** + custom refresh interval. |
| **Settings → Device** | Device name, timezone, orientation, time format, plugin cycle interval — all in a card-grouped form with segmented buttons and an inline cycle-interval readout. |
| **Settings → Wi-Fi setup** | Scan visible networks (deduplicated by SSID — strongest BSSID wins on mesh / dual-band routers), join one, add hidden networks, forget saved ones. Works over BLE for first-time provisioning. |
| **Settings → Content** | **Playlists** (create / edit / time-bound / reorder) and **Plugin order** (customize the cycle order across instances). |
| **Settings → API keys** | OpenAI / weather / calendar tokens — edits `.env` on the Pi. |
| **Settings → Logs** | Two modes: snapshot of the last N hours, or **live tail over Server-Sent Events** with auto-scroll, "Jump to latest" pill when scrolled up, and a service selector. |
| **Settings → Backup / restore** | Snapshot `device.json` to a copyable JSON envelope. Restore replaces it atomically. The Updates screen also takes an **auto-snapshot before every apply** that can be restored from one tap. |
| **Settings → Updates** | Two paths. **git pull + update.sh** with live NDJSON step-by-step progress + collapsible per-stage stdout/stderr on failure. **OTA via GitHub release tarball** (alternative path) for cases where the working tree is dirty / not a git checkout. SSH fallback at the bottom is pre-filled with `ssh inky@<live IP>` from the dashboard. |
| **Settings → Network override** | Pin the Pi's IP / hostname manually when auto-discovery picks the wrong address. |
| **Settings → Appearance** | Inline theme picker — System / Light / Dark. |
| **Settings → About** | Identity + service health snapshot, including whether the BLE secondary advertisement registered cleanly. "Show intro again" replays the onboarding flow. |

### First-time setup walkthrough

1. Flash Raspberry Pi OS Bookworm onto a microSD card and boot the Pi
   (no Wi-Fi configuration needed during flashing).
2. Install InkyPi via the instructions below. After reboot, the Pi will:
   - Try any configured Wi-Fi network for ~45 seconds.
   - If no network is reachable, broadcast its own Wi-Fi hotspot named
     `InkyPi-<hostname>` and start advertising over Bluetooth.
3. Install the companion app on your phone, open it, and tap **Find
   InkyPi**. Pick your device from the scan list (`InkyPi-<hostname>`).
4. On the dashboard, open **Wi-Fi setup**, choose your home network, enter
   the password, and tap **Connect**. The Pi joins your network and the
   hotspot turns itself off.
5. From this point on, BLE handles low-bandwidth commands (settings,
   plugin switching, Wi-Fi changes) and Wi-Fi handles high-throughput
   tasks (image upload, live preview).

### Notes

- BLE image uploads work without any Wi-Fi but are bandwidth-limited
  (~5–15 KB/s on Pi Zero 2 W). For frequent uploads, keep the Pi on a
  shared Wi-Fi network — the app uses HTTP over Wi-Fi automatically when
  available.
- The Pi advertises its current Wi-Fi IP in its BLE advertisement's
  manufacturer data (see
  [docs/bluetooth.md](./docs/bluetooth.md#advertisement-payload--wi-fi-ip-in-manufacturer-data-v1)).
  The app reads it during BLE scan and probes HTTP at that address
  *without ever opening a GATT connection* — when the Pi is reachable
  on Wi-Fi the entire 3-5s BLE handshake can be skipped.
- Avahi also advertises `_inkypi._tcp` on port 80, so apps that prefer
  mDNS over manufacturer data work the same way.
- BLE pairing is JustWorks (no PIN, no Pi-side UI). Companion app on
  Android auto-confirms via a `BluetoothDevice.ACTION_PAIRING_REQUEST`
  receiver so Samsung's persistent pair dialog doesn't pop up on every
  reconnect.
- The hotspot password is auto-generated on first boot and persisted in
  `device.json`. Read it via the **Dashboard** screen in the app.
- See [docs/bluetooth.md](./docs/bluetooth.md) for the full GATT
  protocol spec if you want to write your own client.

## Hardware 
- Raspberry Pi (4 | 3 | Zero 2 W)
    - Recommended to get 40 pin Pre Soldered Header
- MicroSD Card (min 8 GB) like [this one](https://amzn.to/3G3Tq9W)
- E-Ink Display:
    - Inky Impression by Pimoroni
        - **[13.3 Inch Display](https://collabs.shop/q2jmza)**
        - **[7.3 Inch Display](https://collabs.shop/q2jmza)**
        - **[5.7 Inch Display](https://collabs.shop/ns6m6m)**
        - **[4 Inch Display](https://collabs.shop/cpwtbh)**
    - Inky wHAT by Pimoroni
        - **[4.2 Inch Display](https://collabs.shop/jrzqmf)**
    - Waveshare e-Paper Displays
        - Spectra 6 (E6) Full Color **[4 inch](https://www.waveshare.com/4inch-e-paper-hat-plus-e.htm?&aff_id=111126)** **[7.3 inch](https://www.waveshare.com/7.3inch-e-paper-hat-e.htm?&aff_id=111126)** **[13.3 inch](https://www.waveshare.com/13.3inch-e-paper-hat-plus-e.htm?&aff_id=111126)**
        - Black and White **[7.5 inch](https://www.waveshare.com/7.5inch-e-paper-hat.htm?&aff_id=111126)** **[13.3 inch](https://www.waveshare.com/13.3inch-e-paper-hat-k.htm?&aff_id=111126)**
        - See [Waveshare e-paper displays](https://www.waveshare.com/product/raspberry-pi/displays/e-paper.htm?&aff_id=111126) or visit their [Amazon store](https://amzn.to/3HPRTEZ) for additional models. Note that some models like the IT8951 based displays are not supported. See later section on [Waveshare e-Paper](#waveshare-display-support) compatibility for more information.
- Picture Frame or 3D Stand
    - See [community.md](./docs/community.md) for 3D models, custom builds, and other submissions from the community

**Disclosure:** The links above are affiliate links. I may earn a commission from qualifying purchases made through them, at no extra cost to you, which helps maintain and develop this project.

## Installation
To install InkyPi, follow these steps:

1. Clone the repository:
    ```bash
    git clone https://github.com/jeremiahng11/JKL-InkyPi.git
    ```
2. Navigate to the project directory:
    ```bash
    cd JKL-InkyPi
    ```
3. Run the installation script with sudo:
    ```bash
    sudo bash install/install.sh [-W <waveshare device model>]
    ``` 
     Option: 
    
    * -W \<waveshare device model\> - specify this parameter **ONLY** if installing for a Waveshare display.  After the -W option specify the Waveshare device model e.g. epd7in3f.

    e.g. for Inky displays use:
    ```bash
    sudo bash install/install.sh
    ```

    and for [Waveshare displays](#waveshare-display-support) use:
    ```bash
    sudo bash install/install.sh -W epd7in3f
    ```


After the installation is complete, the script will prompt you to reboot your Raspberry Pi. Once rebooted, the display will update to show the InkyPi splash screen.

**Services installed:**
- `inkypi.service` — the Flask web UI and refresh task.
- `inkypi-ble.service` — Bluetooth Low Energy peripheral for the
  companion app. Advertises as `InkyPi-<hostname>`.
- `inkypi-netd.service` — connectivity monitor that toggles the Pi's
  Wi-Fi access point when no upstream network is reachable. Requires
  Raspberry Pi OS Bookworm (NetworkManager).

Note: 
- The installation script requires sudo privileges to install and run the service. We recommend starting with a fresh installation of Raspberry Pi OS to avoid potential conflicts with existing software or configurations.
- The installation process will automatically enable the required SPI and I2C interfaces on your Raspberry Pi.
- Bluetooth and NetworkManager are enabled automatically — no extra steps needed.

For more details, including instructions on how to image your microSD with Raspberry Pi OS, refer to [installation.md](./docs/installation.md). You can also checkout [this YouTube tutorial](https://youtu.be/L5PvQj1vfC4).

## Update

You have three ways to apply updates — pick whichever fits.

### From the companion app — git pull path (default)
- Open the app → **Settings → Updates**.
- If you're behind upstream, tap **Update now**. A modal sheet shows
  each phase (`preflight` → `git pull` → `update.sh`) with live log
  streaming and collapsible per-step output so any failure is
  diagnosable without SSH.
- The app takes an **auto-snapshot of `device.json`** before the apply
  starts, surfaced as a one-tap restore tile on the same screen if
  things go sideways.
- A **Re-apply latest (force)** option is available even when you're
  already at HEAD — useful when systemd unit files or config drifted
  on the Pi and you want to overwrite them from the repo.
- A **Finish update** button appears when a previous run half-finished
  (incomplete-apply detection via the `LAST_UPDATE_MARKER` vs `HEAD`
  comparison) — re-runs `update.sh` against the code already on disk
  to reconcile apt / pip / unit-file state.
- Estimated time: 1-8 minutes depending on whether apt / pip have
  real work to do. Don't power-cycle the Pi while it's running.

### From the companion app — OTA release tarball
- Open the app → **Settings → Updates** → **Apply a tagged release**.
- Lists the most recent GitHub releases for this repo (inferred from
  the Pi's `git remote get-url origin`).
- Tap a tag → confirm → tarball downloads, extracts over the working
  checkout (preserving `.git/`), then runs `install/update.sh`
  with the same streaming progress UI.
- Useful when the Pi's working tree is dirty, when it isn't a git
  checkout at all, or when you want to pin to a specific published
  version without dealing with detached HEAD.

### Manually via SSH
```bash
cd ~/JKL-InkyPi
git pull
sudo bash install/update.sh
```
`update.sh` short-circuits with "Already up to date" when the working
tree hasn't moved since the last applied commit — pass `--force` to
re-apply anyway. It diffs the systemd unit files (`inkypi.service`,
`inkypi-ble.service`, `inkypi-netd.service`) and the avahi service
definition, daemon-reloads + restarts only the units that actually
changed.

All three paths cover the same ground: apt deps, pip deps, the CLI
shim, vendored JS/CSS, systemd units, and the avahi mDNS service
file. The Python source under `src/` is symlinked from
`/usr/local/inkypi/src/` so it picks up `git pull` automatically once
`inkypi.service` restarts.

### Testing `update.sh`

A shell test harness lives at `install/test/run_tests.sh`. It sandboxes
`apt-get` / `sudo` / `systemctl` / `lsb_release` / `tput` via PATH-shim
binaries and exercises seven invariants:

- non-root invocation exits with a clear error
- `--defer-restart` skips `inkypi.service` restart (the path the
  companion app's streaming update uses to avoid SIGTERMing its own
  parent Flask process)
- the default path DOES restart `inkypi.service`
- short-circuits cleanly when `LAST_UPDATE_MARKER` matches `HEAD`
- `--force` bypasses the short-circuit
- missing virtualenv exits with the documented error
- the marker file is written with the current `HEAD` on success

```bash
bash install/test/run_tests.sh
```

`update.sh` honors `INSTALL_PATH`, `BINPATH`, `VENV_PATH`,
`SERVICE_FILE_TARGET`, `LAST_UPDATE_MARKER` and `SKIP_ROOT_CHECK` env
overrides so the harness can sandbox without modifying the script.
All fall back to production defaults on a real install.

## Uninstall
To install InkyPi, simply run the following command:

```bash
sudo bash install/uninstall.sh
```

## Roadmap
The InkyPi project is constantly evolving, with many exciting features and improvements planned for the future.

- Plugins, plugins, plugins
- Modular layouts to mix and match plugins
- Support for buttons with customizable action bindings
- Improved Web UI on mobile devices

Check out the public [trello board](https://trello.com/b/SWJYWqe4/inkypi) to explore upcoming features and vote on what you'd like to see next!

## Waveshare Display Support

Waveshare offers a range of e-Paper displays, similar to the Inky screens from Pimoroni, but with slightly different requirements. While Inky displays auto-configure via the inky Python library, Waveshare displays require model-specific drivers from their [Python EPD library](https://github.com/waveshareteam/e-Paper/tree/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd).

This project has been tested with several Waveshare models. **Displays based on the IT8951 controller are not supported**, and **screens smaller than 4 inches are not recommended** due to limited resolution.

If your display model has a corresponding driver in the link above, it’s likely to be compatible. When running the installation script, use the -W option to specify your display model (without the .py extension). The script will automatically fetch and install the correct driver.

## License

Distributed under the GPL 3.0 License, see [LICENSE](./LICENSE) for more information.

This project includes fonts and icons with separate licensing and attribution requirements. See [Attribution](./docs/attribution.md) for details.

## Issues

Check out the [troubleshooting guide](./docs/troubleshooting.md). If you're still having trouble, feel free to create an issue on the [GitHub Issues](https://github.com/jeremiahng11/JKL-InkyPi/issues) page.

If you're using a Pi Zero W, note that there are known issues during the installation process. See [Known Issues during Pi Zero W Installation](./docs/troubleshooting.md#known-issues-during-pi-zero-w-installation) section in the troubleshooting guide for additional details..

## Acknowledgements

Check out these similar projects:

- [PaperPi](https://github.com/txoof/PaperPi) - awesome project that supports waveshare devices
    - shoutout to @txoof for assisting with InkyPi's installation process
- [InkyCal](https://github.com/aceinnolab/Inkycal) - has modular plugins for building custom dashboards
- [PiInk](https://github.com/tlstommy/PiInk) - inspiration behind InkyPi's flask web ui
- [rpi_weather_display](https://github.com/sjnims/rpi_weather_display) - alternative eink weather dashboard with advanced power efficiency
