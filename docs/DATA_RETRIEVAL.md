# OrderForge — How to Retrieve Captured Data

This guide is for team members (especially Person B — Market Data) who need to pull Bitfinex L3 data for replay and backtesting.

---

## L3 format (dual timestamp)

Each line: `<local_ts_ns>|<exchange_ts_ns>|<channel>|<raw_json>`

- **exchange_ts_ns**: Stamped by exchange at event time (0 when Bitfinex R0 does not provide)
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

### Prerequisites

1. **AWS CLI** installed: `pip install awscli`
2. **AWS credentials** configured. Ask the Capture Engineer (Person A) for:
   - An IAM user with read-only S3 access, or
   - Access key ID + secret access key for the bucket

### Configure AWS CLI (one time)

```bash
aws configure
# Enter: Access Key ID, Secret Access Key, region (eu-central-1)
```

### Download commands

```bash
# List available days
aws s3 ls s3://orderforge-s3-bucket/bitfinex/btcusd/ --recursive

# Download a single day's events.log
aws s3 cp s3://orderforge-s3-bucket/bitfinex/btcusd/2026-03-11/events.log ./data/2026-03-11/

# Download all files for a day (events.log + snapshots)
aws s3 sync s3://orderforge-s3-bucket/bitfinex/btcusd/2026-03-11/ ./data/2026-03-11/

# Download to project data folder
mkdir -p data/2026-03-11
aws s3 sync s3://orderforge-s3-bucket/bitfinex/btcusd/2026-03-11/ ./data/2026-03-11/
```

### Where to put it locally

Use the project's `data/` folder so the market_data module can find it:

```
orderForge/
  data/
    2026-03-11/
      events.log
      snapshot_162715.log   (if any)
```

---

## Option B — Copy from EC2 (fallback)

Use this if S3 uploads are not working yet or you need data before the next upload.

### Prerequisites

- SSH key (`.pem` file) — ask Person A
- EC2 public IP — ask Person A or check AWS Console → EC2 → Instances

### Copy a day's data

```bash
# Replace with your actual key path and EC2 IP
scp -i ~/Desktop/orderforge-key.pem -r ec2-user@3.121.130.22:~/orderforge/data/2026-03-11/ ./data/2026-03-11/
```

### Copy only events.log

```bash
mkdir -p data/2026-03-11
scp -i ~/Desktop/orderforge-key.pem ec2-user@3.121.130.22:~/orderforge/data/2026-03-11/events.log ./data/2026-03-11/
```

---

## Data format (unchanged contract)

Each line in `events.log`:

```
<local_ts_ns>|<channel>|<raw_json>
```

Channels: `bf_snapshot`, `bf_update`, `bf_info`, `bf_subscribed`, `bf_heartbeat`, etc.

Use `market_data/reader.py` helpers: `extract_bitfinex_snapshot_orders()`, `extract_bitfinex_update()`.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Access Denied` on S3 | Ask Person A to grant your IAM user `s3:GetObject` on the bucket |
| `NoSuchBucket` | Confirm bucket name: `orderforge-s3-bucket` (or whatever Person A configured) |
| No files in S3 | Capture may have just started; snapshots upload every 10 min, events.log at midnight UTC |
| SCP "Permission denied" | Ensure you have the correct `.pem` key and EC2 IP |
