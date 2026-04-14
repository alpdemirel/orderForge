# OrderForge — How to Retrieve Captured Data

This guide is for team members (especially Person B — Market Data) who need to pull Bitfinex L3 data for replay and backtesting.

---

## L3 format (dual timestamp)

Each line: `<local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>`

- **exchange_ts_ns**: Stamped by exchange at event time via TIMESTAMP conf flag (0 for legacy data captured before April 13, 2026)
- **local_ts_ns**: Stamped by us at capture time (EC2 colocated with exchange)

---

## Primary vs. supplementary data

| File | Purpose | When uploaded to S3 |
|------|---------|---------------------|
| **events.log** | Full event stream (snapshot + every update). **Use this for replay.** | At UTC midnight (day rotation) |
| **snapshot_*.log** | Order book state at 10‑min intervals (checkpoint/validation) | Every 10 minutes |

**events.log** is the main source for backtesting. Person B should use it for replay. Snapshots are optional.

---

## Where the data lives

| Location | When available | Who can access |
|----------|----------------|----------------|
| **S3** | Snapshots every 10 min; full `events.log` at midnight UTC | Anyone with AWS credentials (see below) |
| **EC2** | Continuously while capture runs | Anyone with SSH key (fallback) |

**Primary method:** Download from S3.  
**Fallback:** Copy from EC2 via SCP if S3 is not yet populated.

---

## Option A — Download from S3 (recommended)

Once the capture service is running with a valid IAM role, data is uploaded to:

```
s3://orderforge-s3-bucket/bitfinex/btcusd/YYYY-MM-DD/
```

Ask the Capture Engineer (Person A) for your IAM access key ID and secret access key before starting.

---

### Step 1 — Install AWS CLI

#### Ubuntu / Debian
```bash
pip install awscli --break-system-packages
```

#### macOS
```bash
pip3 install awscli
```
Or with Homebrew:
```bash
brew install awscli
```

#### Windows (PowerShell)
Download and run the official MSI installer:
```
https://awscli.amazonaws.com/AWSCLIV2.msi
```
Or with winget:
```powershell
winget install Amazon.AWSCLI
```

---

### Step 2 — Configure credentials (one time)

#### Ubuntu / macOS
```bash
aws configure
# AWS Access Key ID: <your key>
# AWS Secret Access Key: <your secret>
# Default region name: eu-central-1
# Default output format: json
```

#### Windows (PowerShell)
```powershell
aws configure
# AWS Access Key ID: <your key>
# AWS Secret Access Key: <your secret>
# Default region name: eu-central-1
# Default output format: json
```

---

### Step 3 — Download data

#### Ubuntu / macOS

```bash
# List available days
aws s3 ls s3://orderforge-s3-bucket/bitfinex/btcusd/

# Create local folder and download a full day (events.log + snapshots)
mkdir -p data/2026-03-15
aws s3 sync s3://orderforge-s3-bucket/bitfinex/btcusd/2026-03-15/ data/2026-03-15/

# Download only events.log for a day
mkdir -p data/2026-03-15
aws s3 cp s3://orderforge-s3-bucket/bitfinex/btcusd/2026-03-15/events.log data/2026-03-15/events.log
```

#### Windows (PowerShell)

```powershell
# List available days
aws s3 ls s3://orderforge-s3-bucket/bitfinex/btcusd/

# Create local folder and download a full day (events.log + snapshots)
New-Item -ItemType Directory -Force -Path data\2026-03-15
aws s3 sync s3://orderforge-s3-bucket/bitfinex/btcusd/2026-03-15/ data\2026-03-15\

# Download only events.log for a day
New-Item -ItemType Directory -Force -Path data\2026-03-15
aws s3 cp s3://orderforge-s3-bucket/bitfinex/btcusd/2026-03-15/events.log data\2026-03-15\events.log
```

### Where to put it locally

Use the project's `data/` folder so the market_data module can find it:

```
orderForge/
  data/
    2026-03-15/
      events.log
      snapshot_162715.log   (if any)
```

> `data/` is in `.gitignore` — these files will never be committed to the repository.

---

## Option B — Copy from EC2 (fallback)

Use this if S3 uploads are not working yet or you need data before the next upload.

**Prerequisites:** SSH key (`.pem` file) and EC2 public IP — ask Person A.

### Copy a full day's data

#### Ubuntu / macOS
```bash
scp -i ~/Desktop/orderforge-key.pem -r ec2-user@<EC2_IP>:~/orderforge/data/2026-03-15/ ./data/2026-03-15/
```

#### Windows (PowerShell)
```powershell
scp -i $HOME\Desktop\orderforge-key.pem -r ec2-user@<EC2_IP>:~/orderforge/data/2026-03-15/ .\data\2026-03-15\
```

### Copy only events.log

#### Ubuntu / macOS
```bash
mkdir -p data/2026-03-15
scp -i ~/Desktop/orderforge-key.pem ec2-user@<EC2_IP>:~/orderforge/data/2026-03-15/events.log ./data/2026-03-15/
```

#### Windows (PowerShell)
```powershell
New-Item -ItemType Directory -Force -Path data\2026-03-15
scp -i $HOME\Desktop\orderforge-key.pem ec2-user@<EC2_IP>:~/orderforge/data/2026-03-15/events.log .\data\2026-03-15\
```

---

## Data format

Each line in `events.log` uses the dual-timestamp format:

```
<local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>
```

- `local_ts_ns` — EC2 wall-clock timestamp (nanoseconds) when message was received
- `exchange_ts_ns` — Exchange-side timestamp (nanoseconds) when the event was created. 0 for legacy data captured before the TIMESTAMP flag was enabled (April 13, 2026).
- Channels: `bf_snapshot`, `bf_update`, `bf_info`, `bf_subscribed`

Legacy 3-field format (`<local_ts_ns>|<channel>|<raw_json>`) is supported by the parser for backwards compatibility.

Use `market_data/reader.py` helpers: `extract_bitfinex_snapshot_orders()`, `extract_bitfinex_update()`.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Access Denied` on S3 | Ask Person A to grant your IAM user `s3:GetObject` on the bucket |
| `NoSuchBucket` | Confirm bucket name: `orderforge-s3-bucket` (or whatever Person A configured) |
| No files in S3 | Capture may have just started; snapshots upload every 10 min, events.log at midnight UTC |
| SCP "Permission denied" | Ensure you have the correct `.pem` key and EC2 IP |
