#!/bin/bash

# Formatting stuff
bold=$(tput bold)
normal=$(tput sgr0)
green=$(tput setaf 2)
red=$(tput setaf 1)

SOURCE=${BASH_SOURCE[0]}
while [ -h "$SOURCE" ]; do # resolve $SOURCE until the file is no longer a symlink
  DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )

APPNAME="inkypi"
INSTALL_PATH="/usr/local/$APPNAME"
BINPATH="/usr/local/bin"
VENV_PATH="$INSTALL_PATH/venv_$APPNAME"

SERVICE_FILE="$APPNAME.service"
SERVICE_FILE_SOURCE="$SCRIPT_DIR/$SERVICE_FILE"
SERVICE_FILE_TARGET="/etc/systemd/system/$SERVICE_FILE"

APT_REQUIREMENTS_FILE="$SCRIPT_DIR/debian-requirements.txt"
PIP_REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

echo_success() {
  echo -e "$1 [\e[32m\xE2\x9C\x94\e[0m]"
}

echo_error() {
  echo -e "$1 [\e[31m\xE2\x9C\x98\e[0m]\n"
}

setup_zramswap_service() {
  echo "Enabling and starting zramswap service."
  sudo apt-get install -y zram-tools > /dev/null
  echo -e "ALGO=zstd\nPERCENT=60" | sudo tee /etc/default/zramswap > /dev/null
  sudo systemctl enable --now zramswap
}

setup_earlyoom_service() {
  echo "Enabling and starting earlyoom service."
  sudo apt-get install -y earlyoom > /dev/null
  sudo systemctl enable --now earlyoom
}

update_app_service() {
  echo "Updating $APPNAME systemd service."
  if [ -f "$SERVICE_FILE_SOURCE" ]; then
    cp "$SERVICE_FILE_SOURCE" "$SERVICE_FILE_TARGET"
    echo "Restarting $APPNAME service."
    sudo systemctl daemon-reload
    sudo systemctl restart $SERVICE_FILE
  else
    echo_error "ERROR: Service file $SERVICE_FILE_SOURCE not found!"
    exit 1
  fi
}

update_cli() {
  cp -r "$SCRIPT_DIR/cli" "$INSTALL_PATH/"
  sudo chmod +x "$INSTALL_PATH/cli/"*
}

# Re-copy the auxiliary systemd unit files (BLE peripheral, network
# fallback). These are not part of update_app_service's single-file
# logic, so without this step a git pull that ships new ExecStartPre
# / environment / restart-policy lines never reaches the running
# system. Idempotent — diffs cause a daemon-reload + restart, no-ops
# are quiet.
update_extra_services() {
  local extras=("inkypi-ble.service" "inkypi-netd.service")
  local changed=0
  for unit in "${extras[@]}"; do
    local src="$SCRIPT_DIR/$unit"
    local dest="/etc/systemd/system/$unit"
    if [ ! -f "$src" ]; then
      continue
    fi
    if ! cmp -s "$src" "$dest" 2>/dev/null; then
      sudo cp "$src" "$dest"
      changed=1
      echo_success "\tUpdated $unit"
    fi
  done
  if [ "$changed" -eq 1 ]; then
    sudo systemctl daemon-reload
    for unit in "${extras[@]}"; do
      if sudo systemctl is-enabled --quiet "$unit"; then
        sudo systemctl restart "$unit" || true
      fi
    done
  fi
}

# Drop or refresh the avahi mDNS service definition so the companion
# app can discover this Pi on Wi-Fi without prior BLE pairing.
# Safe to call on installs that lack avahi-daemon — apt install at
# the top of update.sh will have ensured the package is present.
update_avahi_service() {
  local src="$SCRIPT_DIR/inkypi-avahi.service"
  local dest="/etc/avahi/services/inkypi.service"
  if [ ! -f "$src" ]; then
    return
  fi
  if ! cmp -s "$src" "$dest" 2>/dev/null; then
    sudo cp "$src" "$dest"
    sudo chmod 644 "$dest"
    sudo systemctl restart avahi-daemon > /dev/null 2>&1 || true
    echo_success "\tUpdated Avahi mDNS advertisement (_inkypi._tcp)"
  fi
}

# Get OS release number, e.g. 11=Bullseye, 12=Bookworm, 13=Trixe
get_os_version() {
  echo "$(lsb_release -sr)"
}


# Ensure script is run with sudo
if [ "$EUID" -ne 0 ]; then
  echo_error "ERROR: This script requires root privileges. Please run it with sudo."
  exit 1
fi

APT_LOG="/tmp/inkypi-update-apt.log"
apt-get update -y > "$APT_LOG" 2>&1 \
  || { echo_error "apt-get update failed; see $APT_LOG"; tail -20 "$APT_LOG" >&2; exit 1; }

if [ -f "$APT_REQUIREMENTS_FILE" ]; then
  echo "Installing system dependencies... "
  # Strip comment / blank lines before handing the package list to apt
  # (xargs doesn't understand requirements-file comment syntax).
  grep -vE '^[[:space:]]*(#|$)' "$APT_REQUIREMENTS_FILE" \
    | xargs sudo apt-get install -y > "$APT_LOG" 2>&1 \
    || { echo_error "apt-get install failed; see $APT_LOG"; tail -20 "$APT_LOG" >&2; exit 1; }
  echo_success "Installed system dependencies."
else
  echo_error "ERROR: System dependencies file $APT_REQUIREMENTS_FILE not found!"
  exit 1
fi

# check OS version for Bookworm to setup zramswap
if [[ $(get_os_version) = "12" ]] ; then
  echo "OS version is Bookworm - setting up zramswap"
  setup_zramswap_service
else
  echo "OS version is not Bookworm - skipping zramswap setup."
fi
setup_earlyoom_service

# Check if virtual environment exists
if [ ! -d "$VENV_PATH" ]; then
  echo_error "ERROR: Virtual environment not found at $VENV_PATH. Run the installation script first."
  exit 1
fi

# Activate the virtual environment
source "$VENV_PATH/bin/activate"

# Upgrade pip
PIP_LOG="/tmp/inkypi-update-pip.log"
: > "$PIP_LOG"
echo "Upgrading pip..."
$VENV_PATH/bin/python -m pip install --upgrade pip setuptools wheel >> "$PIP_LOG" 2>&1 \
  || { echo_error "Pip toolchain upgrade failed; see $PIP_LOG"; tail -20 "$PIP_LOG" >&2; exit 1; }
echo_success "Pip upgraded successfully."

# Install or update Python dependencies
if [ -f "$PIP_REQUIREMENTS_FILE" ]; then
  echo "Updating Python dependencies..."
  $VENV_PATH/bin/python -m pip install --upgrade -r "$PIP_REQUIREMENTS_FILE" -qq >> "$PIP_LOG" 2>&1 \
    || { echo_error "Dependency update failed; see $PIP_LOG"; tail -20 "$PIP_LOG" >&2; exit 1; }
  echo_success "Dependencies updated successfully."
else
  echo_error "ERROR: Requirements file $PIP_REQUIREMENTS_FILE not found!"
  exit 1
fi

echo "Updating executable in ${BINPATH}/$APPNAME"
cp $SCRIPT_DIR/inkypi $BINPATH/
sudo chmod +x $BINPATH/$APPNAME

echo "Update JS and CSS files"
bash $SCRIPT_DIR/update_vendors.sh > /dev/null

update_app_service
update_extra_services
update_avahi_service
update_cli

echo_success "Update completed."
