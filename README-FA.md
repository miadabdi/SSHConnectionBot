# SSHConnection v2 (فارسی)

این پوشه نسخه بازطراحی‌شده ربات SSH تلگرام با معماری تمیز است.

## شروع سریع

```bash
cd v2
make init
make keygen
# کلید تولید شده را در .env به عنوان ENCRYPTION_KEY قرار دهید
# مقدار BOT_TOKEN را در .env تنظیم کنید
make up
make logs
```

## دستورات اصلی

- `/connect`، `/disconnect [name|all]`، `/switch`، `/status`، `/history`
- `/save`، `/quick`، `/servers`، `/delserver`
- `/group`، `/groups`، `/delgroup`
- `/macro`، `/macros`، `/run`، `/delmacro`
- `/download`، آپلود با کپشن `/upload <remote_path>`
- `/monitor`، `/shell`، `/exit`

برای جزئیات کامل به `README.md` مراجعه کنید.
