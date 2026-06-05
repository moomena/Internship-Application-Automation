#!/usr/bin/env python3
"""
Headless CLI monitor — runs without Streamlit.
Useful for servers / cron jobs.
Usage: python monitor_cli.py [--once]
"""
import sys
import time
import signal
import argparse
from pathlib import Path
import os
os.chdir(Path(__file__).parent)

from core.database import init_db, get_settings
from core.orchestrator import run_monitor_cycle, start_monitor, stop_monitor
from loguru import logger

init_db()

parser = argparse.ArgumentParser(description="Internship Agent CLI Monitor")
parser.add_argument("--once", action="store_true", help="Run one scan then exit")
args = parser.parse_args()

def handle_sig(sig, frame):
    logger.info("Stopping monitor...")
    stop_monitor()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_sig)
signal.signal(signal.SIGTERM, handle_sig)

if args.once:
    logger.info("Running single scan...")
    run_monitor_cycle()
    logger.info("Done.")
    sys.exit(0)

settings = get_settings()
interval = int(settings.get("check_interval_minutes", 10))
logger.info("Starting continuous monitor (interval: {} min)", interval)

while True:
    run_monitor_cycle()
    logger.info("Next scan in {} minutes...", interval)
    time.sleep(interval * 60)
