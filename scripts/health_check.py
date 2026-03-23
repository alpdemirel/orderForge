import sys
import time
import os
import json
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone
import logging
import boto3

logging.basicConfig(
    filename="health_alarm.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] ALARM: %(message)s",
)

def send_alert(message: str) -> None:
    sns_topic_arn = os.getenv("AWS_SNS_TOPIC_ARN")
    if sns_topic_arn:
        try:
            sns = boto3.client('sns', region_name=os.getenv("AWS_REGION", "eu-central-1"))
            sns.publish(
                TopicArn=sns_topic_arn,
                Message=message,
                Subject="OrderForge EC2 ALARM"
            )
            logging.info("Sent AWS SNS email alert.")
        except Exception as e:
            logging.error(f"Failed to send AWS SNS alert: {e}")

def check_health(base_dir: Path, max_age_seconds: int = 300) -> None:
    now_utc = datetime.now(timezone.utc)
    current_date = now_utc.strftime("%Y-%m-%d")
    
    events_file = base_dir / current_date / "events.log"
    
    if not events_file.exists():
        msg = f"events.log for {current_date} does not exist at {events_file.resolve()}!"
        print(msg)
        logging.error(msg)
        send_alert(f"[OrderForge EC2 ALARM] ⚠️ {msg}")
        sys.exit(1)
        
    mtime = events_file.stat().st_mtime
    age_seconds = time.time() - mtime
    
    if age_seconds > max_age_seconds:
        msg = f"events.log is stale! Last modified {age_seconds:.1f} seconds ago. Max allowed: {max_age_seconds}s."
        print(msg)
        logging.error(msg)
        send_alert(f"[OrderForge EC2 ALARM] 🚨 {msg}")
        sys.exit(2)
        
    print(f"Health check PASSED. File modified {age_seconds:.1f} seconds ago.")

if __name__ == "__main__":
    data_dir = Path("data")
    if len(sys.argv) > 1:
        data_dir = Path(sys.argv[1])
        
    # Also log to console so systemd timer records it
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] ALARM: %(message)s"))
    logging.getLogger().addHandler(console_handler)
    
    check_health(data_dir)
