# Compressing and Archiving Daily Logs

**Status: IMPLEMENTED** (in `capture/main.py` since March 2026)

When a day ends and a new daily folder is created:
1. The previous day's folder is compressed into a single `.tar.gz` file.
2. The compressed `.tar.gz` file is uploaded to the S3 bucket.
3. The local `.tar.gz` is deleted after successful upload.
4. Local directories older than 1 day are cleaned up automatically.

## Implementation details

### `archive_previous_day(base_dir, date_str)` in `capture/main.py`

Called via `run_in_executor()` at UTC midnight rotation so the WebSocket listener is not blocked.

Steps:
1. Compress the entire `base_dir / date_str` folder into `base_dir / date_str.tar.gz`
2. Upload the `.tar.gz` file to S3 using `upload_to_s3()`
3. Delete the local `.tar.gz` after successful upload
4. Call `clean_old_directories()` to remove folders older than 1 day

### `clean_old_directories(base_dir)` in `capture/main.py`

Iterates over directories in `base_dir`, parses folder names as `YYYY-MM-DD` dates, and removes
any that are more than 1 day old from the current UTC date.

### Trigger point

In `file_writer()`, when `now_date != current_date` (UTC midnight crossing):

```python
asyncio.get_event_loop().run_in_executor(
    None, archive_previous_day, base_dir, current_date
)
```

This runs the compression/upload/cleanup in a background thread, non-blocking.

### S3 key format

```
s3://<S3_BUCKET>/<S3_PREFIX>/YYYY-MM-DD/YYYY-MM-DD.tar.gz
```

Example: `s3://orderforge-s3-bucket/bitfinex/btcusd/2026-04-14/2026-04-14.tar.gz`
