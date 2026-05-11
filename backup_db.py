#!/usr/bin/env python3
"""
Run this script daily (e.g., via Render Cron Job or a scheduled task)
to backup the SQLite databases to /data/backups/
"""
import os
import shutil
from datetime import datetime

BASE_DIR = '/data' if os.path.exists('/data') else '.'
DB_PATH = os.path.join(BASE_DIR, 'consultations.db')
FEEDBACK_DB_PATH = os.path.join(BASE_DIR, 'feedback.db')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')

def backup_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    if os.path.exists(DB_PATH):
        backup_file = os.path.join(BACKUP_DIR, f'consultations_{timestamp}.db')
        shutil.copy2(DB_PATH, backup_file)
        print(f"Backed up consultations.db to {backup_file}")
    if os.path.exists(FEEDBACK_DB_PATH):
        backup_feedback = os.path.join(BACKUP_DIR, f'feedback_{timestamp}.db')
        shutil.copy2(FEEDBACK_DB_PATH, backup_feedback)
        print(f"Backed up feedback.db to {backup_feedback}")

if __name__ == '__main__':
    backup_db()