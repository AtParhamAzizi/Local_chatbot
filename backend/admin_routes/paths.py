"""Static administrator asset paths, independent of model configuration."""

from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
ADMIN_DIR = str(BASE_DIR / 'admin')
ADMIN_USERS_DIR = str(BASE_DIR / 'admin-users')
