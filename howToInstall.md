# Run Local RAG Chatbot on Ubuntu Server

This guide deploys the current project directly on **Ubuntu Server 24.04 LTS,
x86-64**, using **CPython 3.12, MySQL, systemd, and nginx with HTTPS**. Commands
are for Bash on the Ubuntu server, run by an SSH user with `sudo` privileges.
Do not run the Windows `.cmd` launcher or copy a Windows virtual environment.

This is a deployment procedure based on the repository, not a claim that this
guide has been executed on a fresh Ubuntu server. The recorded dependency checks
and their limits are in `requirements/VALIDATION.md`.

## What the project actually runs

- `backend/main.py` starts both services in one production Gunicorn worker with
  eight threads: chat on port **5000**, administration on port **8000**.
- Flask serves `frontend/` directly. **Node.js, npm, a frontend build, and a
  separate frontend server are not required.**
- MySQL stores knowledge records, PDF chunks, embeddings, accounts, sessions,
  conversation history, feedback, and application logs.
- Text models must exist locally before startup. The default pair is
  `Qwen/Qwen3-Embedding-0.6B` and `Qwen/Qwen3.5-2B`.
- Voice is integrated into the chat service. Whisper may download its own model
  on first use; no separate speech server is required.
- Schema upgrades and first-administrator creation are **explicit commands**;
  application startup does neither.

The resulting request paths are:

```text
Browser -> HTTPS nginx -> 127.0.0.1:5000 (chat and voice)
Browser -> HTTPS nginx -> 127.0.0.1:8000 (administration)
Application -> 127.0.0.1:3306 (MySQL)
```

## 1. Prepare the server and DNS

Use a server with sufficient CPU, RAM, and disk for both models plus MySQL.
For initial CPU sizing, budget **at least 12 GB RAM**, preferably more, and
**30 GB free disk** as a planning allowance, not a measured minimum. CUDA
dependencies, backups, larger models, and growing knowledge bases need more.
CPU generation can be slow; a compatible NVIDIA GPU is optional (section 13).

Check the host:

```bash
cat /etc/os-release
uname -m
free -h
df -h / /tmp
```

Expect `x86_64`. The recorded Linux dependency locks require **Python 3.12**;
do not substitute another Ubuntu release's default Python without checking it.
ARM64 is outside this guide's locked native-install target.

Choose two hostnames and point their DNS A records at this server:

```text
chat.example.com
admin.example.com
```

Replace these names everywhere below. If you publish AAAA records, IPv6 must
also reach this server. The certificate procedure needs publicly reachable DNS
and TCP port 80; private-only installations need an appropriate DNS challenge
or an internally trusted TLS certificate instead.

## 2. Install Ubuntu packages and allow network access

```bash
sudo apt update
sudo apt install -y git ca-certificates curl openssl python3.12 python3.12-venv \
  libgomp1 libstdc++6 mysql-server mysql-client nginx ufw snapd
sudo systemctl enable --now mysql nginx
python3.12 --version
```

If `python3.12-venv` is unavailable on Ubuntu 24.04, enable the Ubuntu Universe
repository using your server's approved package-management process, then retry.
Do not use `sudo pip` or install application packages into system Python.

Before enabling the firewall, allow your **actual SSH port**. The following
assumes standard OpenSSH port 22; adapt it first if your server uses another port:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

Keep the current SSH session open and check a second SSH connection. Also allow
80/443 and your SSH port in the hosting provider's firewall/security group.
**Do not open 3306, 5000, or 8000 publicly.** Keep MySQL's listener local; inspect
`/etc/mysql/mysql.conf.d/mysqld.cnf` if necessary.

## 3. Copy the project and create a service account

Create a dedicated account and an empty deployment directory:

```bash
sudo useradd --system --user-group --no-create-home \
  --home-dir /opt/local-chatbot --shell /usr/sbin/nologin chatbot
sudo install -d -o chatbot -g chatbot -m 0750 /opt/local-chatbot
```

Clone your repository, replacing the placeholder with its actual clone address:

```bash
sudo -u chatbot -H git clone YOUR_REPOSITORY_CLONE_URL /opt/local-chatbot
```

Alternatively, transfer the project into that directory with your normal secure
file-transfer tool, then assign ownership:

```bash
sudo chown -R chatbot:chatbot /opt/local-chatbot
```

Do not transfer `.venv`, `.venv-tests`, caches, or Windows dependency bundles.
Local model directories and runtime JSON preferences are ignored by Git, so
cloning does **not** transfer them. Copy model directories separately in step 5
if you already have them. If the service account/directory already exists, inspect
it and reuse it rather than blindly repeating account creation or overwriting it.

## 4. Create a fresh Python environment and install the CPU profile

```bash
sudo -u chatbot -H bash -c '
set -e
cd /opt/local-chatbot
python3.12 -m venv .venv
.venv/bin/python -B scripts/dependencies.py install --profile cpu
.venv/bin/python -B scripts/dependencies.py verify --profile cpu
'
```

The profile includes web, production serving, MySQL authentication, PDF, and
speech dependencies. It installs hash-checked Linux wheels; do not follow it
with a generic `pip install -r requirements.txt` or unrelated packages.
Internet access to PyPI and the official PyTorch wheel index is needed unless
you prepare an offline wheelhouse as described in `requirements/README.md`.

The installer expects a **new** environment. If installation is interrupted,
preserve it for diagnosis and retry in a new environment, not over a populated
one. Allow ample temporary disk space, especially for CUDA; a small memory-backed
`/tmp` can fill up during installation.

If you intend to use NVIDIA generation from the start, use section 13's
`cu118` commands **instead of** installing this CPU profile.

## 5. Install the complete local text models

Required layout, with case-sensitive directory names:

```text
/opt/local-chatbot/backend/model/Qwen3-Embedding-0.6B/
/opt/local-chatbot/backend/modelgen/qwen3.5-2B/
```

Each directory must contain its own `config.json`, tokenizer files, and **all**
weight files, including shard indexes when present. A config file alone is not
a model, and Git LFS pointer files are not actual weights.

Either copy your existing complete directories from the current machine and
make them readable by `chatbot`, or download them on the server with the Hub
library already included in the dependency profile:

```bash
sudo -u chatbot -H /opt/local-chatbot/.venv/bin/python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="Qwen/Qwen3-Embedding-0.6B",
    local_dir="/opt/local-chatbot/backend/model/Qwen3-Embedding-0.6B",
)
snapshot_download(
    repo_id="Qwen/Qwen3.5-2B",
    local_dir="/opt/local-chatbot/backend/modelgen/qwen3.5-2B",
)
PY
```

These are large downloads. For repeatable deployments, record and use approved
model commit IDs via the `revision` argument; Python wheel locks do not pin model
weights. Review the model licenses for your intended use. The application loads
these text models offline and will not repair missing downloads at startup.

Check the files:

```bash
sudo -u chatbot test -f /opt/local-chatbot/backend/model/Qwen3-Embedding-0.6B/config.json
sudo -u chatbot test -f /opt/local-chatbot/backend/modelgen/qwen3.5-2B/config.json
sudo du -sh /opt/local-chatbot/backend/model /opt/local-chatbot/backend/modelgen
```

If you copied `backend/model_selection.json` from another installation, inspect
it: its saved choice takes precedence over `GEN_MODEL_ID`. Either provide the
selected model too, or intentionally reset/change that preference for this host.

## 6. Create MySQL accounts and an empty database

Generate **three different** random values in a private terminal: one for the
application DB account, one for the migration DB account, and one session secret.
For each value run:

```bash
openssl rand -hex 32
```

Store them securely; do not paste real secrets into source control, command
arguments, shared logs, or this guide. Hex values are convenient here because
they avoid SQL, Bash, and systemd quoting ambiguities.

Open the local MySQL root session with client history disabled:

```bash
sudo env MYSQL_HISTFILE=/dev/null mysql
```

Run the SQL below, replacing the two password placeholders with different values:

```sql
CREATE DATABASE chatbot_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'chatbot_app'@'localhost' IDENTIFIED BY 'REPLACE_APP_DB_PASSWORD';
CREATE USER 'chatbot_migrator'@'localhost' IDENTIFIED BY 'REPLACE_MIGRATOR_DB_PASSWORD';
GRANT SELECT, INSERT, UPDATE, DELETE ON chatbot_db.* TO 'chatbot_app'@'localhost';
GRANT SELECT, INSERT, UPDATE, CREATE, ALTER, INDEX, REFERENCES ON chatbot_db.* TO 'chatbot_migrator'@'localhost';
EXIT;
```

The runtime account has no schema-changing privileges. The migration account
is used only during explicit maintenance. No root database password or remote
root account is required by the application. Do not use the fresh-database SQL
to overwrite an existing installation; use the upgrade instructions in section 14.

Verify that the runtime account can connect over the same TCP path as the app:

```bash
mysql --host=127.0.0.1 --user=chatbot_app --password --database=chatbot_db \
  --execute='SELECT 1;'
```

Enter the password at the prompt, not after `--password`.

## 7. Write protected production settings

Create the configuration file once, outside the repository:

```bash
sudo install -o root -g chatbot -m 0640 /dev/null /etc/local-chatbot.env
sudoedit /etc/local-chatbot.env
```

Add the following, replacing the password, secret, and domain placeholders:

```ini
APP_ENV=production
APP_HOST=127.0.0.1
DB_HOST=127.0.0.1
DB_PORT=3306
DB_NAME=chatbot_db
DB_USER=chatbot_app
DB_PASS=REPLACE_APP_DB_PASSWORD
ADMIN_SESSION_SECRET=REPLACE_RANDOM_SESSION_SECRET
UI_ALLOWED_ORIGINS=https://chat.example.com
ADMIN_ALLOWED_ORIGINS=https://admin.example.com
TRUSTED_PROXY_HOPS=1
TRUSTED_PROXY_CIDRS=127.0.0.1/32
GEN_DEVICE=cpu
STT_MODEL=base
STT_LANGUAGE=fa
STT_DEVICE=cpu
STT_COMPUTE_TYPE=int8
RETENTION_CHAT_DAYS=30
RETENTION_IP_DAYS=0
RETENTION_FEEDBACK_DAYS=30
RETENTION_LOG_DAYS=14
RETENTION_DELETE_ENABLED=1
RETENTION_INTERVAL_SECONDS=3600
RETENTION_BATCH_SIZE=1000
RETENTION_MAX_BATCHES=100
```

Use a session secret of at least 32 characters; the generated 64-character hex
value meets that requirement. Use exact origins without paths, trailing slashes,
or wildcards. Keep this file in simple `KEY=value` form, without `export` or shell
expansions, so both Bash and systemd interpret it consistently.

**Important:** Python does not automatically read `.env` files. systemd will load
this file for the service; the management commands below explicitly source it.
The repository's `.env.example` is primarily for Compose and is not a complete
native production configuration.

Review the retention values **before first startup**: automatic deletion is
enabled. The defaults expire chat/feedback after 30 days and logs after 14 days,
and do not store raw IPs. A value of zero disables persistence of that category;
it does **not** mean unlimited retention. See the privacy section in `README.md`
before changing the policy.

The proxy settings above trust exactly one nginx proxy connecting over IPv4
loopback. They are not suitable unchanged for Docker, an external proxy, a CDN,
or a different proxy chain; never replace the trusted CIDR with all networks.

## 8. Apply migrations, then create the first administrator

For the **empty database created in step 6**, run:

```bash
sudo -u chatbot -H bash -c '
set -e
set -a
source /etc/local-chatbot.env
set +a
cd /opt/local-chatbot
export DB_USER=chatbot_migrator
read -r -s -p "Migration database password: " DB_PASS
printf "\n"
export DB_PASS
.venv/bin/python -B backend/schema_migrations.py upgrade
.venv/bin/python -B backend/schema_migrations.py status
'
```

Enter the **migration** account password. For this checkout, successful status
shows `schema_version` and `latest_version` both **4**, with empty `pending` and
`incomplete` lists. `status` alone can exit successfully while upgrades remain
pending, so inspect its output. For any existing/nonempty database, stop and use
section 14: the runner requires a restore-tested backup, not just a rerun.

Create the initial administrator using the runtime account:

```bash
sudo -u chatbot -H bash -c '
set -e
set -a
source /etc/local-chatbot.env
set +a
cd /opt/local-chatbot
exec .venv/bin/python backend/setup_admin.py
'
```

Use an interactive SSH terminal (not redirected stdin). The username is
**`administrator`**. Choose and confirm a unique password of at least **12
characters**. There is no default password; rerunning setup leaves existing
accounts unchanged. Migration and administrator setup do not load the text models.

## 9. Run the application automatically with systemd

```bash
sudoedit /etc/systemd/system/local-chatbot.service
```

Add:

```ini
[Unit]
Description=Local RAG Chatbot
After=network-online.target mysql.service
Wants=network-online.target
Requires=mysql.service

[Service]
Type=simple
User=chatbot
Group=chatbot
WorkingDirectory=/opt/local-chatbot
EnvironmentFile=/etc/local-chatbot.env
Environment=HOME=/opt/local-chatbot
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/local-chatbot/.venv/bin/python -B /opt/local-chatbot/backend/main.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=60
KillMode=mixed
UMask=0027
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Start and inspect it:

```bash
sudo systemd-analyze verify /etc/systemd/system/local-chatbot.service
sudo systemctl daemon-reload
sudo systemctl enable --now local-chatbot
sudo systemctl status local-chatbot --no-pager
sudo journalctl -u local-chatbot -f
```

Press Ctrl+C to leave the journal viewer; it does not stop the service. Wait for
model initialization and a generator-ready message. A systemd `active` status
alone does not establish model readiness. Startup only verifies the schema; if
it reports migrations are needed, return to step 8 rather than granting DDL to
the runtime account.

Check locally in another SSH session:

```bash
curl --fail http://127.0.0.1:5000/health
sudo ss -ltnp
```

Expect `{"status":"ok"}` and application listeners on `127.0.0.1:5000` and
`127.0.0.1:8000`, not public addresses. The health endpoint is a basic service
check, not a full chat, voice, or database acceptance test. Production admin
access over plain HTTP is intentionally rejected; finish HTTPS before login.

**Do not start extra Gunicorn workers, use preloading, or launch the UI/admin
modules separately.** The supplied entry point shares model memory and in-process
state across the two listeners using one worker.

## 10. Configure nginx and HTTPS

First create a temporary HTTP-only site so certificate validation can work
without exposing the application before TLS is ready:

```bash
sudoedit /etc/nginx/sites-available/local-chatbot
```

Paste, replacing both hostnames:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name chat.example.com admin.example.com;
    access_log off;
    location / {
        return 503;
    }
}
```

Enable it on a fresh nginx installation:

```bash
sudo ln -s /etc/nginx/sites-available/local-chatbot /etc/nginx/sites-enabled/local-chatbot
sudo nginx -t
sudo systemctl reload nginx
sudo snap install --classic certbot
sudo /snap/bin/certbot --nginx --redirect --cert-name local-chatbot \
  -d chat.example.com -d admin.example.com
```

Use the snap Certbot installation consistently; do not also install a second
Certbot with apt or into the application environment. Follow its email/terms
prompts. If issuance fails, check DNS, stale AAAA records, inbound port 80, and
provider firewalls. Do not continue until the certificate exists. On a server
already hosting sites, inspect its configuration before adding symlinks or sites.

Now **replace the contents** of `/etc/nginx/sites-available/local-chatbot` with
the final configuration below, using your real hostnames. The certificate name
above gives the certificate paths used here:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name chat.example.com admin.example.com;
    access_log off;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name chat.example.com;
    ssl_certificate /etc/letsencrypt/live/local-chatbot/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/local-chatbot/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    access_log off;
    client_max_body_size 12m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    location = /voice-stream {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name admin.example.com;
    ssl_certificate /etc/letsencrypt/live/local-chatbot/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/local-chatbot/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    access_log off;
    client_max_body_size 55m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

Apply it and test certificate renewal:

```bash
sudo nginx -t
sudo systemctl reload nginx
sudo /snap/bin/certbot renew --dry-run
sudo systemctl list-timers --all
```

Confirm that a Certbot renewal timer is scheduled. The nginx plugin manages
renewal/reload; retain the certificate lineage and its configuration. The 55 MiB
admin proxy limit leaves multipart overhead above the application's 50 MiB PDF
limit; application-specific PDF/JSON limits still apply.

This example assumes nginx is the only proxy. It **overwrites** forwarded client
and scheme headers instead of trusting values supplied by a browser. Preserve
the browser's `Origin` header; do not remove it to bypass origin failures.
Prefer restricting the admin hostname to a VPN or an administrator IP allowlist
in addition to application authentication. If adding nginx access restrictions,
ensure certificate renewal challenges remain reachable and retest renewal.

## 11. Verify chat, administration, and voice

```bash
curl --fail https://chat.example.com/health
curl -I https://admin.example.com/login
```

Then use a browser:

1. Open `https://admin.example.com/login` and sign in as `administrator` with
   the password from step 8. Do not open admin HTML files directly from disk.
2. Import a small JSON knowledge base from the dashboard, for example:

   ```json
   [
     {"question": "What is this service?", "answer": "This is our local knowledge chatbot."}
   ]
   ```

3. Open `https://chat.example.com`, ask the imported question, and confirm a reply.
   Check that history/feedback work in the same browser session. An empty database
   will not contain your existing Windows knowledge base until you import or
   restore it separately.
4. Allow microphone access and test a short Persian recording. The first visit
   to `/voice/health` may start a Whisper `base` download; wait for `ready`.
5. In browser developer tools, confirm `/voice-stream` upgrades to WebSocket
   (HTTP 101). Voice requires browser HTTPS, not an HTTP server-IP URL.

Voice defaults to CPU/int8, including with GPU text generation. If downloads
are prohibited, prepopulate the Whisper cache as the `chatbot` account or set
`STT_MODEL` to a complete local CTranslate2 Whisper directory; then add
`STT_LOCAL_FILES_ONLY=1` to the environment file and restart. The cache is under
`backend/STT/.models`. Do not enable Hub-wide offline mode before any required
Whisper download. Do not run `backend/STT/STT.py` alongside this app on port 5000.

## 12. Everyday service commands

```bash
sudo systemctl status local-chatbot --no-pager
sudo journalctl -u local-chatbot -n 100 --no-pager
sudo systemctl restart local-chatbot
sudo systemctl stop local-chatbot
sudo systemctl start local-chatbot
```

After editing `/etc/local-chatbot.env`, restart the service. After editing the
unit file, run `sudo systemctl daemon-reload` and then restart. Keep production
secrets outside Git and restrict backups. Runtime writes need access to the
backend directory for `model_selection.json`, `controller_state.json`, and the
Whisper cache; the service-account ownership in this guide permits that.

## 13. Optional NVIDIA text-generation profile

CPU is the default and does not require NVIDIA software. The repository's
`cu118` profile targets the existing older-GPU CUDA 11.8 configuration; it is
**not a universal profile for every newer NVIDIA GPU**. See
`requirements/README.md` and `requirements/VALIDATION.md` for qualification limits.
Linux GPU hardware acceptance must be performed on your server.

Install a maintained, compatible NVIDIA driver using Ubuntu's supported driver
process, reboot if required, and first confirm:

```bash
nvidia-smi
```

The wheel profile supplies its CUDA runtime; a separate CUDA Toolkit is not
required for text generation. Create a **separate, fresh** environment:

```bash
sudo -u chatbot -H bash -c '
set -e
cd /opt/local-chatbot
python3.12 -m venv .venv-cu118
.venv-cu118/bin/python -B scripts/dependencies.py install --profile cu118
.venv-cu118/bin/python -B scripts/dependencies.py verify --profile cu118
'
```

Do not use `--software-only` as hardware acceptance: the normal verifier must
pass its actual FP16/NF4 GPU execution checks. Do not install CPU and CUDA
profiles over each other. Keep the working CPU environment until acceptance.

Set these in `/etc/local-chatbot.env`:

```ini
GEN_DEVICE=cuda
GEN_QUANTIZATION=auto
STT_DEVICE=cpu
STT_COMPUTE_TYPE=int8
```

Change the unit's `ExecStart` interpreter to
`/opt/local-chatbot/.venv-cu118/bin/python`, keeping its other arguments. Reload
systemd, restart, and confirm the journal reports `[gen] ready on cuda`.
`GEN_DEVICE=cuda` requires working GPU generation rather than silently falling
back to CPU. The embedding model remains on CPU. Small GPUs automatically use
NF4 quantization; long contexts can still exceed available VRAM. Keeping STT on
CPU avoids the separate CUDA/cuDNN requirements of GPU speech inference.

## 14. Backups, moving existing data, and upgrades

**Do not treat transferring the repository as transferring MySQL data.** Model
files, MySQL data, and runtime preferences are separate deployment assets.
Preserve your old deployment and a tested backup before changing an existing
database. Migrations are forward-only, not an automatic rollback mechanism.

For the local MySQL installation used in this guide, stop all writers and create
a protected dump (choose a new filename for each backup):

```bash
sudo systemctl stop local-chatbot
sudo install -d -o root -g chatbot -m 0750 /var/backups/local-chatbot
sudo bash -c 'umask 027; mysqldump --single-transaction --routines --triggers --events --hex-blob --no-tablespaces --set-gtid-purged=OFF --default-character-set=utf8mb4 chatbot_db > /var/backups/local-chatbot/before-upgrade.sql'
sudo chown root:chatbot /var/backups/local-chatbot/before-upgrade.sql
sudo chmod 0640 /var/backups/local-chatbot/before-upgrade.sql
sudo test -s /var/backups/local-chatbot/before-upgrade.sql
```

Check the dump command's exit status **before continuing**. These commands use
Ubuntu's local MySQL root socket authentication, not application credentials.
Do not add `--databases`: the dump should restore into an isolated database name.

Restore-test it into a newly named, isolated database that no app is using:

```bash
sudo mysql --execute='CREATE DATABASE chatbot_restore_check CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;'
sudo bash -c 'mysql --database=chatbot_restore_check < /var/backups/local-chatbot/before-upgrade.sql'
```

Compare table row counts and representative Unicode records, embedding bytes,
account hashes, permissions, and feedback between the source and restored copy.
A nonempty file alone is not proof of a usable backup. Secure and later expire
the restore-test database under your retention policy.

To move existing data from Windows/another server, dump it with that server's
MySQL tools and credentials, securely transfer the dump, and restore into a
**fresh target database** before migration. Do not import it on top of a newly
initialized application schema. Preserve existing administrator accounts; do not
expect a new default password. Follow `backend/migrations/README.md` for the
supported legacy schema, backup, and recovery requirements.

For an application upgrade, keep writers stopped, install the reviewed release,
and prepare/verify a fresh dependency environment if its profile changed. Run
the migration command from step 8 with this change:

```bash
.venv/bin/python -B backend/schema_migrations.py upgrade --backup /var/backups/local-chatbot/before-upgrade.sql
.venv/bin/python -B backend/schema_migrations.py status
```

These two lines belong **inside the same environment-loading, migration-account
shell from step 8**, not an unconfigured terminal. If using the CUDA environment,
substitute its interpreter path. Verify preserved data before restarting, because
retention resumes at startup. Only after verification:

```bash
sudo systemctl start local-chatbot
sudo journalctl -u local-chatbot -n 100 --no-pager
```

For interrupted migrations, follow `backend/migrations/README.md` before using
`--resume`; do not edit checksums, mark versions applied by hand, or delete the
database to work around an error. Keep protected off-server backups, the matching
old application release, and copies of the runtime JSON settings. Database
retention does not automatically expire dumps, MySQL binary logs, systemd/nginx
logs, or provider backups.

## 15. Troubleshooting

| Symptom | Check / action |
| --- | --- |
| Missing required settings or allowed origins | Load `/etc/local-chatbot.env` for CLI commands; confirm the unit's `EnvironmentFile`; origins must be exact and path-free. |
| Unsupported Python/target or installer refuses an existing environment | Use CPython 3.12 x86-64 and a fresh virtual environment; do not reuse Windows `.venv`. |
| MySQL access denied | Check the prompted account password, `DB_HOST`, account host matching, and grants; test with `mysql --host=127.0.0.1 ... --password`. |
| Schema version / backup / incomplete migration error | Use the explicit migration command and inspect status; existing data requires a restore-tested backup. See the migration recovery guide. |
| Missing config/tokenizer/weights | Check the exact case-sensitive model paths, complete shards, permissions, and any saved model selection. |
| nginx 502 or a restarting worker | Inspect `journalctl -u local-chatbot`, MySQL availability, model loading, and memory. Gunicorn's configured worker timeout is 300 seconds. |
| Process killed / out of memory | Inspect `sudo journalctl -k -n 100`; add RAM or reduce model/context requirements. Repeated restarts do not fix insufficient memory. |
| Admin HTTPS/origin rejection or login cookie not retained | Use the exact HTTPS admin hostname, preserve `Host`/`Origin`, overwrite forwarding headers as shown, and keep the trusted proxy at `127.0.0.1/32`. Do not disable Secure cookies. |
| PDF upload receives 413 from nginx | Check `client_max_body_size 55m` on the admin server; the app still limits PDFs to 50 MiB, 200 pages, and 256 chunks. |
| Voice unavailable or WebSocket fails | Check `/voice/health`, Whisper download/cache permissions, HTTPS microphone permission, and nginx upgrade headers/timeouts. |
| CUDA verifier or generation fails | Check driver/GPU compatibility and the separate `cu118` environment; `nvidia-smi` alone does not establish NF4 compatibility or sufficient VRAM. |
| Application answers but has no old knowledge/history | A Git clone does not copy the database; import knowledge or restore a protected database backup. |

## Note about the included Docker configuration

Docker is an alternative, not an additional requirement for this native setup.
The repository includes `Dockerfile`, `compose.yaml`, and `compose.gpu.yaml` and
documents their local workflow in `README.md`. **The supplied Compose file sets
`APP_ENV=development`**, even though the image defaults to production. Do not
expose that unchanged setup as an Ubuntu production deployment. Production
containers need explicit production/origin settings, a correctly trusted HTTPS
proxy with the actual container peer network, explicit migrations, and backup
planning. Do not run native and container copies on the same ports/database as
independent active writers.

## References used for this guide

Project behavior was checked against `backend/main.py`, `backend/serving.py`,
`backend/settings.py`, `backend/bootstrap.py`, `backend/schema_migrations.py`,
`backend/setup_admin.py`, `backend/model_registry.py`, `backend/security.py`,
`requirements/README.md`, `requirements/VALIDATION.md`, `README.md`, and the
container configuration. Upstream instructions checked on November 6, 2026:

- Ubuntu Python 3.12 venv package: `https://packages.ubuntu.com/noble/python3.12-venv`
- Ubuntu MySQL installation: `https://ubuntu.com/server/docs/install-and-configure-a-mysql-server/`
- Ubuntu nginx installation: `https://ubuntu.com/server/docs/how-to/web-services/install-nginx/`
- nginx WebSocket proxying: `https://nginx.org/en/docs/http/websocket.html`
- Certbot nginx/snap installation and renewal: `https://certbot.eff.org/instructions?os=snap&ws=nginx`
- Hugging Face local model downloads: `https://huggingface.co/docs/huggingface_hub/en/guides/download`
