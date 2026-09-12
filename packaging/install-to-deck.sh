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

sudo mkdir -p "${PLUGIN_DIR}/dist"
sudo cp "${STAGING}/plugin.json" "${STAGING}/package.json" "${STAGING}/main.py" "${PLUGIN_DIR}/"
sudo cp "${STAGING}/dist/index.js" "${PLUGIN_DIR}/dist/"
sudo chown -R root:root "${PLUGIN_DIR}"

# Only seed settings if absent, so a reinstall never clobbers your gateway URL.
if [ -f "${STAGING}/settings.json" ] && ! sudo test -f "${SETTINGS_DIR}/settings.json"; then
    sudo mkdir -p "${SETTINGS_DIR}"
    sudo cp "${STAGING}/settings.json" "${SETTINGS_DIR}/settings.json"
    sudo chown -R deck:deck "${SETTINGS_DIR}"
    echo "Seeded settings: $(cat "${STAGING}/settings.json")"
else
    echo "Kept existing settings."
fi

sudo systemctl restart plugin_loader
echo "Done. Decky is restarting — Shelfcloud should appear in the Decky menu."
