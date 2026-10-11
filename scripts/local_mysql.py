"""Explicit, opt-in local MySQL setup. Never overwrites an existing .env."""
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as file:
        file.write(text)


def main():
    os.chdir(ROOT)
    (ROOT / 'instance').mkdir(exist_ok=True)
    if not (ROOT / '.env').exists():
        write_private(ROOT / '.env', 'DB_HOST=127.0.0.1\nDB_PORT=3307\nDB_NAME=phishing_awareness_tracker\nDB_USER=awareness_dev\nDB_PASSWORD=' + secrets.token_hex(24) + '\nBASE_URL=http://127.0.0.1:5055\n')
    if not (ROOT / 'instance/mysql.env').exists():
        write_private(ROOT / 'instance/mysql.env', 'MYSQL_ROOT_PASSWORD=' + secrets.token_hex(32) + '\n')
    subprocess.run(['docker', 'compose', '--env-file', '.env', '--env-file', 'instance/mysql.env', 'up', '-d', '--wait'], check=True)
    print('Local MySQL is healthy. Existing .env values were preserved. SMTP remains unconfigured.')


if __name__ == '__main__':
    main()
