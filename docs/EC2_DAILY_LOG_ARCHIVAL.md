# Compressing and Archiving Daily Logs

The goal is to modify the `capture/main.py` script so that when a day ends and a new daily folder is created:
1. The previous day's folder is compressed into a single `.tar.gz` file.
2. The compressed `.tar.gz` file is uploaded to the S3 bucket.
3. The original uncompressed daily folder on the local EC2 server is deleted to save space.

## Proposed Changes

### [MODIFY] `capture/main.py`
We need to update the file rotation logic and add archiving logic.

- **Import additions:** Add `tarfile` and `shutil` (for creating tar archives and deleting directories).
- **Update `file_writer`:**
  - Currently, when `now_date != current_date`, it closes the daily `events.log` and uploads it to S3 immediately.
  - We will modify this so that when the day rotates, it delegates the cleanup of the *previous* day's folder to an asynchronous background task, so we don't block the WebSocket listener from capturing new events.
- **New `archive_previous_day` function:**
  - Compress the entire `base_dir / current_date` folder into `base_dir / current_date.tar.gz`.
  - Upload the `.tar.gz` file to S3 using a new `boto3` helper (`upload_to_s3`).
  - Use `shutil.rmtree` to delete the local `{base_dir}/{current_date}` folder.
  - Optionally, delete the local `.tar.gz` file after a successful S3 upload, or keep it depending on requirements (deleting is better for space).

## Verification Plan

### Automated Tests
- Run `pytest` to ensure no existing logic was broken (e.g. `test_capture_writer.py` and `test_capture_format.py` might need minor updates if they assert specific file uploads).

### Manual Verification
- We can run the capture script locally, simulate a day change by temporarily modifying the date logic, and observe if:
  - The previous folder is compressed into a `.tar.gz` file.
  - The uncompressed folder is deleted.
  - S3 upload is triggered for the `.tar.gz` file (this will just print an info log locally without the S3 variable).
