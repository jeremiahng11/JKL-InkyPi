# InkyPi 

<img src="./docs/images/inky_clock.jpg" />


## About InkyPi 
InkyPi is an open-source, customizable E-Ink display powered by a Raspberry Pi. Designed for simplicity and flexibility, it allows you to effortlessly display the content you care about, with a simple web interface that makes setup and configuration effortless.

**Features**:
- Natural paper-like aethetic: crisp, minimalist visuals that are easy on the eyes, with no glare or backlight
- Web Interface allows you to update and configure the display from any device on your network
- **Bluetooth control via the InkyPi Companion app** — set up Wi-Fi, swap plugins, upload images, and tweak settings from your phone with no web browser needed
- **Wi-Fi hotspot fallback** — when InkyPi has no upstream network it broadcasts its own access point so you're never locked out, even on a fresh install
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

| Screen | Capability |
|--------|------------|
| **Find InkyPi** | Single list of every reachable Pi — Wi-Fi targets (mDNS + cached IP + IP from BLE-advertisement manufacturer data, deduplicated) on top, Bluetooth scan results below. Wi-Fi is auto-preferred when reachable. |
| **Dashboard** | Live status: Wi-Fi mode + SSID + IP, e-ink resolution, current plugin. Inline preview of what's on the display with refresh, force re-render, and skip-next buttons. CPU / memory / disk / load stats card auto-pauses when you're on a different tab. |
| **Photos** | Gallery of every uploaded image flattened across instances. Tap to view full-size, display now, or delete. FAB opens the Upload flow. |
| **Playlists** | Create / edit / time-bound / reorder playlists. Tap any plugin instance to push it to the display immediately. |
| **Upload image** | Pick from gallery / camera / URL. Transport picker shows whether you're going over Wi-Fi (fast) or Bluetooth (slow); cellular-only state surfaces a "join the same Wi-Fi for ~1000× faster uploads" hint. Optional **Save to playlist** + custom refresh interval. |
| **Settings → Wi-Fi setup** | Scan visible networks, join one, add hidden networks, forget saved ones. Works over BLE for first-time provisioning. |
| **Settings → Plugins** | Browse plugins, switch the active one, reorder the listing the "New instance" picker shows. |
| **Settings → API keys** | OpenAI / weather / calendar tokens — edits `.env` on the Pi. |
| **Settings → Logs** | Tail the `inkypi.service` journal and share / copy. |
| **Settings → Backup / restore** | Snapshot `device.json` to a copyable JSON envelope. Restore replaces it atomically. |
| **Settings → Updates** | Shows commits behind upstream + the short changelog. **Update now** button triggers `git pull && install/update.sh` remotely with live step-by-step progress + collapsible per-stage stdout/stderr on failure — no SSH needed. |
| **Settings → Network override** | Pin the Pi's IP / hostname manually when auto-discovery picks the wrong address. |
| **Settings → About** | Identity + service health snapshot, including whether the BLE secondary advertisement registered cleanly. |

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

You have two ways to apply updates — pick whichever fits.

### From the companion app (recommended)
- Open the app → **Settings → Updates**.
- If you're behind upstream, tap **Update now**. A modal sheet shows
  each phase (`preflight` → `git pull` → `update.sh`) with collapsible
  per-step output so any failure is diagnosable without SSH.
- A **Re-apply latest (force)** option is available even when you're
  already at HEAD — useful when systemd unit files or config drifted
  on the Pi and you want to overwrite them from the repo.
- Estimated time: 1-8 minutes depending on whether apt / pip have
  real work to do. Don't power-cycle the Pi while it's running.

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

Both paths cover the same ground: apt deps, pip deps, the CLI
shim, vendored JS/CSS, systemd units, and the avahi mDNS service
file. The Python source under `src/` is symlinked from
`/usr/local/inkypi/src/` so it picks up `git pull` automatically once
`inkypi.service` restarts.

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
