# OrderForge — Bitfinex L3 Capture: Complete Step-by-Step Guide
## Everything Alp needs to do, in order

---

## PART 1 — Local code changes 

##DONE-DO PART 2 NOW

### Step 1.1 — Replace capture/main.py

Copy the file `bitfinex_main.py` (provided alongside this guide) into your repo as:
```
capture/main.py
```
This completely replaces the Coinbase stub. Delete everything that was there.

Key differences from the old file:
- Connects to `wss://api-pub.bitfinex.com/ws/2` instead of Coinbase
- Subscribes with `prec: "R0"` for individual order IDs (true L3)
- Writes 5 channel labels: `bf_snapshot`, `bf_update`, `bf_info`, `bf_subscribed`, `bf_heartbeat`
- Maintains in-memory `book_state` dict for 10-minute snapshots
- Handles UTC midnight file rotation
- Uploads to S3 via boto3 (only if S3_BUCKET env var is set)
- No auth needed — Bitfinex R0 Raw Books is a public feed

### Step 1.2 — Change market_data/reader.py


- `extract_bitfinex_snapshot_orders(event)` — parses snapshot into list of order dicts
- `extract_bitfinex_update(event)` — parses a single update into an order dict
- Tan (Person B) uses these in the replay engine

### Step 1.3 — Update pyproject.toml / setup.cfg dependencies

Add `boto3` to your dependencies. Find your `pyproject.toml` or `setup.cfg` and add:

```toml
# pyproject.toml
[project]
dependencies = [
    "websockets>=12.0",
    "boto3>=1.34",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-asyncio"]
```

Or if you use setup.cfg:
```ini
[options]
install_requires =
    websockets>=12.0
    boto3>=1.34
```

### Step 1.4 — Test locally (no S3 needed yet)

```bash
cd your-repo-root
python3 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# Run capture locally — writes to ./data/YYYY-MM-DD/events.log
python -m capture.main ./data
```

You should see:
```
2026-03-08 14:00:01 [INFO] Connecting to wss://api-pub.bitfinex.com/ws/2 ...
2026-03-08 14:00:02 [INFO] Connected. Enabling checksums and subscribing ...
2026-03-08 14:00:02 [INFO] Writer opened event log: data/2026-03-08/events.log
```

After a few seconds, check the file:
```bash
head -5 data/2026-03-08/events.log
```

You should see lines like:
```
1741600802123456789|bf_subscribed|{"event":"subscribed","channel":"book",...}
1741600802234567890|bf_snapshot|[123456,[...]]
1741600802345678901|bf_update|[123456,12345678901,43250.5,0.5]
```

Stop with Ctrl-C. If this works, your capture code is correct.

### Step 1.5 — Commit and push

```bash
git add capture/main.py market_data/reader.py pyproject.toml
git commit -m "feat(capture): switch to Bitfinex R0 Raw Books L3 source"
git push
```

---

## PART 2 — AWS setup (one-time, do this before launching EC2)

Go to https://aws.amazon.com and sign in (or create a free account).

### Step 2.1 — Create an S3 bucket

1. Open the AWS Console → search "S3" → click "Create bucket"
2. Bucket name: `orderforge-capture-YOURNAME` (must be globally unique, e.g. `orderforge-capture-alp`)
3. Region: `eu-central-1` (Frankfurt) — closest to Bitfinex servers
4. Block all public access: ✅ ON (leave default)
5. Versioning: off
6. Click "Create bucket"

Note your bucket name — you'll use it as `S3_BUCKET` env var later.

### Step 2.2 — Create an IAM role for EC2

This lets your EC2 instance write to S3 without hardcoding credentials.

1. AWS Console → search "IAM" → click "Roles" → "Create role"
2. Trusted entity type: "AWS service"
3. Use case: "EC2" → Next
4. Permissions: search "AmazonS3" → select "AmazonS3FullAccess"
   (For production you'd scope this to one bucket, but for a class project this is fine)
5. Role name: `OrderforgeEC2Role`
6. Click "Create role"

### Step 2.3 — Launch an EC2 instance

1. AWS Console → search "EC2" → "Launch instance"
2. Name: `orderforge-capture`
3. AMI: "Amazon Linux 2023 AMI" (free tier eligible, 64-bit x86)
4. Instance type: `t3.micro` (free tier: 750 hours/month for 12 months)
5. Key pair: create a new key pair
   - Name: `orderforge-key`
   - Type: RSA
   - Format: `.pem` (Linux/Mac) or `.ppk` (Windows/PuTTY)
   - **Download and save the .pem file — you can never download it again**
6. Network settings:
   - Allow SSH traffic from: "My IP" (or 0.0.0.0/0 for simplicity — less secure)
   - No need to open HTTP/HTTPS ports for capture
7. Storage: 20 GB gp3 (default is fine)
8. Advanced details → IAM instance profile → select `OrderforgeEC2Role`
9. Click "Launch instance"

Wait ~2 minutes for the instance to reach "running" state.

### Step 2.4 — Note your instance's public IP

EC2 Console → your instance → copy the "Public IPv4 address" (3.121.130.22).

### Step 2.5 — Attach IAM role to existing EC2 (if you skipped it at launch)

If your instance's IAM role is empty, S3 uploads will fail. Attach the role:

1. AWS Console → EC2 → Instances
2. Select your `orderforge-capture` instance
3. Actions → Security → **Modify IAM role**
4. IAM role: select `OrderforgeEC2Role` (create it first via Step 2.2 if needed)
5. Update IAM role

No reboot needed. Restart the capture service: `sudo systemctl restart orderforge-capture`

---

## PART 3 — Connect to EC2 and set up the environment

### Step 3.1 — SSH into the instance

**Mac/Linux:**
```bash
chmod 400 ~/Desktop/orderforge-key.pem
ssh -i ~/Desktop/orderforge-key.pem ec2-user@3.121.130.22 ##THE NUMBER ON THE RIGHT IS THE REAL ADDRESS USE THIS DIRECTLY CHANGE ONLY THE FILE PATH OF THE orderforge-key.pem according to your real file path, ec2-user part is also true.
```

**Windows (PowerShell):**
```powershell
ssh -i C:\Users\You\Downloads\orderforge-key.pem ec2-user@54.123.45.67
```

You should see the Amazon Linux welcome banner.

### Step 3.2 — Install Python and Git

```bash
sudo dnf update -y
sudo dnf install -y python3.11 python3.11-pip python3.11-devel git
python3.11 --version  # should print 3.11.x
```

### Step 3.3 — Clone your repo

```bash
cd ~
git clone https://github.com/YOUR-ORG/YOUR-REPO.git orderforge
cd orderforge
```

Replace `YOUR-ORG/YOUR-REPO` with your actual GitHub repo URL.

### Step 3.4 — Create virtual environment and install dependencies

```bash
cd ~/orderforge
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .[dev]

# Verify boto3 and websockets are installed
python -c "import boto3, websockets; print('deps OK')"
```

### Step 3.5 — Test capture locally on EC2 (no S3 yet)

```bash
cd ~/orderforge
source .venv/bin/activate
python -m capture.main ./data
```

Let it run for 30 seconds, then Ctrl-C. Check the output:
```bash
ls -lh data/$(date -u +%Y-%m-%d)/
cat data/$(date -u +%Y-%m-%d)/events.log | head -5
```

If you see `bf_snapshot` and `bf_update` lines, the feed is working correctly from EC2.

---

## PART 4 — Enable S3 uploads

### Step 4.1 — Set S3_BUCKET and test upload

```bash
export S3_BUCKET="orderforge-s3-bucket"   
export S3_PREFIX="bitfinex/btcusd"

# Run for 30 seconds
python -m capture.main ./data
```

You should see log lines like:
```
2026-03-08 14:10:00 [INFO] Uploaded data/2026-03-08/snapshot_141000.log -> s3://orderforge-capture-alp/bitfinex/btcusd/2026-03-08/snapshot_141000.log
```

Verify in the AWS Console: S3 → your bucket → browse to `bitfinex/btcusd/2026-03-11/`.

---

## PART 5 — Run as a persistent background service (systemd)

You cannot leave an SSH terminal open 24/7. Use systemd to run capture as a service that survives SSH disconnects and reboots.

### Step 5.1 — Copy the service file to systemd

The file `orderforge-capture.service` is provided alongside this guide.

```bash
# Edit the service file first to set your bucket name
nano ~/orderforge/orderforge-capture.service
```

Find this line and update it:
```
Environment="S3_BUCKET=your-orderforge-bucket-name"
```
→ change to your actual bucket name, which is:
```
Environment="S3_BUCKET=orderforge-s3-bucket"
```

Save (Ctrl-X → Y → Enter), then:

```bash
sudo cp ~/orderforge/orderforge-capture.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable orderforge-capture   # auto-start on reboot
sudo systemctl start orderforge-capture
```

### Step 5.2 — Verify the service is running

```bash
sudo systemctl status orderforge-capture
```

You should see `Active: active (running)`. To watch live logs:
```bash
sudo journalctl -u orderforge-capture -f
```

Press Ctrl-C to stop watching (the service keeps running).

### Step 5.3 — Other useful service commands

```bash
# Stop the service
sudo systemctl stop orderforge-capture

# Restart after a code update
sudo systemctl restart orderforge-capture

# See the last 100 log lines
sudo journalctl -u orderforge-capture -n 100

# Check how long it's been running
sudo systemctl status orderforge-capture | grep Active
```

---

## PART 6 — Pulling captured data for replay (for Tan, Person B)

Other team members can download the day's data from S3 to their local machine:

```bash
# Install AWS CLI (one time)
pip install awscli

# Configure with your AWS credentials (ask Alp for the access key)
aws configure

# Download a day's event log
aws s3 cp s3://orderforge-capture-alp/bitfinex/btcusd/2026-03-08/events.log ./local-data/

# Download all files for a day
aws s3 sync s3://orderforge-capture-alp/bitfinex/btcusd/2026-03-08/ ./local-data/2026-03-08/

# List what's in the bucket
aws s3 ls s3://orderforge-capture-alp/bitfinex/btcusd/ --recursive
```

---

## PART 7 — Understanding the Bitfinex L3 data format

### Initial snapshot (channel: bf_snapshot)

```json
[123456, [[12345678901, 43250.5, 0.5], [12345678902, 43249.0, 1.2], [12345678903, 43251.0, -0.3]]]
```

- `123456` = Bitfinex channel ID (assigned at subscription)
- Each inner array = `[ORDER_ID, PRICE, AMOUNT]`
- Positive AMOUNT = bid (someone wants to BUY at that price)
- Negative AMOUNT = ask (someone wants to SELL at that price)

### Order update (channel: bf_update)

```json
[123456, 12345678904, 43252.0, -2.5]
```
→ New ask order with ID `12345678904` at price `43252.0`, amount `-2.5` (2.5 BTC for sale)

```json
[123456, 12345678901, 0, 1]
```
→ Order `12345678901` removed (price = 0 means remove). This happens on fill or cancel.

### Reconstructing the order book

To reconstruct the full order book from a day's events:
1. Find the earliest `bf_snapshot` event — this gives you the initial state
2. Apply every subsequent `bf_update` in timestamp order:
   - If PRICE != 0: add or update the order
   - If PRICE == 0: remove the order

### Useful links

- Bitfinex Raw Books docs: https://docs.bitfinex.com/reference/ws-public-raw-books
- Bitfinex WebSocket overview: https://docs.bitfinex.com/docs/ws-general
- Bitfinex pair naming: https://docs.bitfinex.com/docs/ws-public (tBTCUSD = BTC/USD spot)
- AWS EC2 Getting Started: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EC2_GetStarted.html
- AWS S3 User Guide: https://docs.aws.amazon.com/AmazonS3/latest/userguide/GetStartedWithS3.html
- AWS IAM Roles for EC2: https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/iam-roles-for-amazon-ec2.html
- systemd service guide: https://www.freedesktop.org/software/systemd/man/systemd.service.html

---

## PART 8 — What each team member does next

**Alp (Capture, Person A):**
- Complete Parts 1–5 above
- Confirm S3 uploads are working
- Share the S3 bucket name with the team
- Set up an IAM user with read-only S3 access for Tan and Bora (Console → IAM → Users → Create)

**Tan (Market Data, Person B):**
- Use `market_data/reader.py` helpers (`extract_bitfinex_snapshot_orders`, `extract_bitfinex_update`)
- Build the replay engine that reads day files from S3 or local
- Key ordering rule for Bitfinex: use `local_ts_ns` as primary sort key (Bitfinex does not provide exchange sequence numbers in the R0 feed, only order IDs)
- Handle the "find earliest snapshot, then apply updates" replay pattern described in Part 7

**Bora (Exchange, Person C):**
- No changes needed — the exchange engine receives normalized `MarketEvent` objects from Tan's replay, not raw Bitfinex frames
- The `order_id` from Bitfinex maps cleanly to your `Order.order_id` field (it's a numeric string)

**Mehmet Kaan (Integration, Person D):**
- Fix the `base.py` bug: `self.exchange.receive_order(...)` → `self.exchange.submit_order(order)` (the gateway method is `submit_order`, not `receive_order`)
- Write integration tests using sample `bf_snapshot` + `bf_update` event files as fixtures

---

## PART 9 — Monitoring checklist (ongoing)

Check these every few days:

```bash
# 1. Is the service still running?
sudo systemctl status orderforge-capture

# 2. Is it actually writing data?
ls -lh ~/orderforge/data/$(date -u +%Y-%m-%d)/events.log

# 3. Is S3 receiving files?
aws s3 ls s3://orderforge-capture-alp/bitfinex/btcusd/$(date -u +%Y-%m-%d)/

# 4. How big is today's data?
du -sh ~/orderforge/data/$(date -u +%Y-%m-%d)/

# 5. Any errors in the last hour?
sudo journalctl -u orderforge-capture --since "1 hour ago" | grep ERROR
```

Expected data volume: ~500 MB–2 GB per day of raw L3 events for BTC/USD on Bitfinex.
