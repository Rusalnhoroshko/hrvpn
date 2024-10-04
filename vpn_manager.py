# vpn_manager.py
import os
import logging
from datetime import datetime, timezone
import asyncio

from dotenv import load_dotenv
from outline_vpn.outline_vpn import OutlineVPN

# Load environment variables
load_dotenv()

OUTLINE_API = os.getenv('OUTLINE_API')
CERT_SHA256 = os.getenv('CERT_SHA256')

# Initialize the OutlineVPN manager
manager = OutlineVPN(api_url=OUTLINE_API, cert_sha256=CERT_SHA256)

async def create_vpn_key_with_name(user_id):
    try:
        key = manager.create_key()
        manager.rename_key(key.key_id, f"User_{user_id}_{datetime.now(timezone.utc).isoformat()}")
        key = manager.get_key(key.key_id)
        key_data = {
            "id": key.key_id,
            "name": key.name,
            "accessUrl": key.access_url,
        }
        logging.info(f"Key created for user {user_id}: {key_data}")
        return key_data
    except Exception as e:
        logging.error(f"Error creating VPN key: {e}")
        return None
