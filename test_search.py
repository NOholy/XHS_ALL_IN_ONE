import sys
import logging
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.security import decrypt_text
from backend.app.models import PlatformAccount, AccountCookieVersion
from backend.app.adapters.xhs.cli_pc_api_adapter import CliXhsPcApiAdapter

logging.basicConfig(level=logging.INFO)

engine = create_engine('sqlite:///data/spider_xhs.db')
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

cookie_version = db.scalars(
    select(AccountCookieVersion)
    .join(PlatformAccount, PlatformAccount.id == AccountCookieVersion.platform_account_id)
    .where(PlatformAccount.sub_type == 'pc')
    .order_by(AccountCookieVersion.created_at.desc())
).first()

if not cookie_version:
    print("No cookies found in DB")
    sys.exit(1)

cookies = decrypt_text(cookie_version.encrypted_cookies)
adapter = CliXhsPcApiAdapter(cookies)

success, msg, payload = adapter.search_note("搭子")
print("Success:", success)
if success and payload and payload.get("data", {}).get("items"):
    item = payload["data"]["items"][0]
    import json
    print(json.dumps(item, indent=2, ensure_ascii=False))
else:
    print("No items or failed", msg)
