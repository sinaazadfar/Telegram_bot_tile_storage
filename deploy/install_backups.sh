#!/bin/sh
set -eu
root=/opt/deldel-tile-bot
# Windows-created release archives may carry 0666 file modes. Privileged imports
# and their containing directories must never be writable by the bot account.
chown -R root:root "$root/bot" "$root/deploy" "$root/tests"
find "$root/bot" "$root/deploy" "$root/tests" -type d -exec chmod 755 {} +
find "$root/bot" "$root/deploy" "$root/tests" -type f -exec chmod 644 {} +
chown root:root "$root/build_output.py" "$root/requirements.txt"
chmod 644 "$root/build_output.py" "$root/requirements.txt"
install -d -m 755 /usr/local/libexec
install -d -m 700 -o deldel-bot -g deldel-bot /var/lib/deldel-tile-backup
install -d -m 700 -o root -g root /var/lib/deldel-tile-backup-root
install -d -m 750 -o root -g deldel-bot /etc/deldel-tile-bot
if [ ! -L /etc/deldel-tile-bot.env ]; then
    mv /etc/deldel-tile-bot.env /etc/deldel-tile-bot/config.env
    ln -s /etc/deldel-tile-bot/config.env /etc/deldel-tile-bot.env
fi
chown root:deldel-bot /etc/deldel-tile-bot/config.env
chmod 640 /etc/deldel-tile-bot/config.env
install -m 700 -o root -g root "$root/deploy/restore_helper.py" /usr/local/libexec/deldel-tile-restore.py
mkdir -p /etc/systemd/system/deldel-tile-bot.service.d
cat > /etc/systemd/system/deldel-tile-bot.service.d/backups.conf <<'EOF'
[Service]
ReadWritePaths=/var/lib/deldel-tile-backup
EOF
cat > /etc/systemd/system/deldel-tile-restore.service <<'EOF'
[Unit]
Description=Restricted Deldel backup restore worker
After=network-online.target

[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=/opt/deldel-tile-bot
ExecStart=/opt/deldel-tile-bot/.venv/bin/python -B /usr/local/libexec/deldel-tile-restore.py
TimeoutStartSec=360
UMask=0077
PrivateTmp=true
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/deldel-tile-bot /etc/deldel-tile-bot /var/lib/deldel-tile-backup /var/lib/deldel-tile-backup-root
EOF
cat > /etc/systemd/system/deldel-tile-restore.path <<'EOF'
[Unit]
Description=Watch for confirmed Deldel restore requests

[Path]
PathExists=/var/lib/deldel-tile-backup/restore-request.json
Unit=deldel-tile-restore.service

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemd-analyze verify /etc/systemd/system/deldel-tile-restore.service /etc/systemd/system/deldel-tile-restore.path
systemctl enable --now deldel-tile-restore.path
