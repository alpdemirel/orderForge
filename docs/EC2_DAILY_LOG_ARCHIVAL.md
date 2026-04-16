# Compressing and Archiving Daily Logs

**Status: IMPLEMENTED** (in `capture/main.py` since March 2026)

When a day ends and a new daily folder is created:
1. The previous day's folder is compressed into a single `.tar.gz` file (events.log + snapshots).
2. The compressed `.tar.gz` file is uploaded to the S3 bucket.
3. Local directories older than 2 days are cleaned up automatically.

On **shutdown** (service restart, Ctrl-C), the current day is archived and uploaded before exit.

## Implementation details

### `archive_previous_day(base_dir, date_str)` in `capture/main.py`

Called via `run_in_executor()` at UTC midnight rotation so the WebSocket listener is not blocked.
The executor future is monitored via `_log_archive_result` callback so exceptions are logged.

Steps:
1. Compress the entire `base_dir / date_str` folder into `base_dir / date_str.tar.gz`
2. Upload the `.tar.gz` file to S3 using `upload_to_s3()`
3. Call `clean_old_directories()` to remove local folders older than 2 days

### `clean_old_directories(base_dir)` in `capture/main.py`

Iterates over directories in `base_dir`, parses folder names as `YYYY-MM-DD` dates, and removes
any that are more than 2 days old from the current UTC date.

### Shutdown archive

In `file_writer()`, on `CancelledError`, `archive_previous_day()` is called for the current day
before the coroutine exits. This prevents data loss when the service is restarted mid-day.

### Trigger point

In `file_writer()`, when `now_date != current_date` (UTC midnight crossing):

```python
loop = asyncio.get_event_loop()
fut = loop.run_in_executor(
    None, archive_previous_day, base_dir, current_date
)
fut.add_done_callback(_log_archive_result)
```

This runs the compression/upload/cleanup in a background thread, non-blocking.

### S3 key format

```
s3://<S3_BUCKET>/<S3_PREFIX>/YYYY-MM-DD/YYYY-MM-DD.tar.gz
s3://<S3_BUCKET>/<S3_PREFIX>/YYYY-MM-DD/snapshot_HHMMSS.log
```

Example: `s3://orderforge-s3-bucket/bitfinex/btcusd/2026-04-14/2026-04-14.tar.gz`
