#!/bin/bash
# Install Shelfcloud into Decky on this Steam Deck.
#
# Decky's plugin directory is root-owned, so this needs sudo. Run it ON the Deck:
#   ~/shelfcloud-staging/install-to-deck.sh
#
# Files are expected to have been staged into ~/shelfcloud-staging first.

set -euo pipefail

PLUGIN_DIR=/home/deck/homebrew/plugins/Shelfcloud
SETTINGS_DIR=/home/deck/homebrew/settings/Shelfcloud
STAGING="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing Shelfcloud from ${STAGING}"

sudo mkdir -p "${PLUGIN_DIR}/dist" "${PLUGIN_DIR}/py_modules" "${PLUGIN_DIR}/bin"
sudo cp "${STAGING}/plugin.json" "${STAGING}/package.json" "${STAGING}/main.py" "${PLUGIN_DIR}/"
sudo cp "${STAGING}/dist/index.js" "${PLUGIN_DIR}/dist/"
sudo cp "${STAGING}"/py_modules/*.py "${PLUGIN_DIR}/py_modules/"
# Bytecode compiled by whichever python staged it, which is not the one the
# loader runs. Harmless until the day it is not.
sudo rm -rf "${PLUGIN_DIR}/py_modules/__pycache__"
sudo cp "${STAGING}"/bin/*.py "${PLUGIN_DIR}/bin/"
sudo chmod +x "${PLUGIN_DIR}"/bin/*.py
if [ -d "${STAGING}/extensions" ]; then
    sudo cp -r "${STAGING}/extensions" "${PLUGIN_DIR}/extensions"
fi
sudo chown -R root:root "${PLUGIN_DIR}"

# Settings hold the API token once signed in, so never clobber an existing file.
if [ -f "${STAGING}/settings.json" ] && ! sudo test -f "${SETTINGS_DIR}/settings.json"; then
    sudo mkdir -p "${SETTINGS_DIR}"
    sudo cp "${STAGING}/settings.json" "${SETTINGS_DIR}/settings.json"
    sudo chown -R deck:deck "${SETTINGS_DIR}"
    echo "Seeded settings: $(cat "${STAGING}/settings.json")"
else
    echo "Kept existing settings (sign-in preserved)."
fi

# Close the settings directory to other plugins.
#
# settings.json holds the bearer token, and Decky drops to the `deck` user for
# EVERY plugin backend — so at the default 0644 any other installed plugin
# could read this account's credential straight off the disk, with no
# escalation needed. New files are written 0600 by the plugin itself; this
# repairs the ones already on disk, including installs that predate that.
if sudo test -d "${SETTINGS_DIR}"; then
    sudo chmod 700 "${SETTINGS_DIR}"
    sudo find "${SETTINGS_DIR}" -maxdepth 1 -type f -name '*.json*' -exec chmod 600 {} +
    echo "Settings locked to the deck user only."
fi

sudo systemctl restart plugin_loader
echo "Done. Decky is restarting — open Shelfcloud from the Decky menu."
