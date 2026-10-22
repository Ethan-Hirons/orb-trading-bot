# ORB Bot — Run Sheet

Server: `root@157.180.31.101`
Paper: `/opt/orb-bot` (auto-arms) · Live: `/opt/orb-bot-live` (manual arm only)
Repo: `C:\Users\ethan\Claude\Projects\Trading bot`

---

## 1. Evening before — arm live

PowerShell, in the repo folder:

```powershell
cd "C:\Users\ethan\Claude\Projects\Trading bot"
.\arm-live.ps1 -Next     # arm the next trading day
.\arm-live.ps1 -Status   # confirm it took
```

`.\arm-live.ps1` = arm today · `-Off` = cancel. No arm = no live session (paper runs regardless).

---

## 2. During the session — watch it

```bash
ssh root@157.180.31.101
```

```bash
# follow live
tail -f /opt/orb-bot-live/logs/orb_$(date +%F).log

# last 60 lines
tail -60 /opt/orb-bot-live/logs/orb_$(date +%F).log

# both instances at once (phone-friendly)
for d in /opt/orb-bot /opt/orb-bot-live; do echo "== $d"; tail -20 $d/logs/orb_$(date +%F).log; done
```

Swap `-live` out of the path for the paper instance.

---

## 3. Checks

```bash
# day summary
grep -A6 "END-OF-DAY SUMMARY" /opt/orb-bot-live/logs/orb_$(date +%F).log

# trail activity
grep -E "TRAIL (ACTIVATED|DEFERRED|EXIT)" /opt/orb-bot-live/logs/orb_$(date +%F).log

# arm status
cd /opt/orb-bot-live && sudo -u orb .venv/bin/python arm.py --status

# invariants
cd /opt/orb-bot-live && ./.venv/bin/python invariants.py

# service state
systemctl status orb-bot-live --no-pager
journalctl -u orb-bot-live -n 50 --no-pager
```

---

## 4. After the close — pull logs down

```powershell
cd "C:\Users\ethan\Claude\Projects\Trading bot"
.\sync-logs.ps1
```

Manual fallback:

```powershell
scp -r root@157.180.31.101:/opt/orb-bot/logs/* "C:\Users\ethan\Claude\Projects\Trading bot\logs\"
scp root@157.180.31.101:/opt/orb-bot/state/trade_history.csv "C:\Users\ethan\Claude\Projects\Trading bot\state\"
scp root@157.180.31.101:/opt/orb-bot/state/daily_state.json "C:\Users\ethan\Claude\Projects\Trading bot\state\"
```

Local review:

```powershell
python invariants.py
Select-String -Path ("logs\orb_{0}.log" -f (Get-Date -Format 'yyyy-MM-dd')) -Pattern "CORRELATION DATA|CORRELATED |Day done|TRAIL "
```

---

## 5. Deploying a change

Laptop:

```powershell
cd "C:\Users\ethan\Claude\Projects\Trading bot"
git status
git add -A
git commit -m "your message"
git push
```

Server (per instance — push first or you deploy stale code):

```bash
cd /opt/orb-bot-live
git pull
chown -R orb:orb /opt/orb-bot-live
```

Hard reset to origin (the Saturday clean-up):

```bash
cd /opt/orb-bot && sudo -u orb git fetch origin && sudo -u orb git reset --hard origin/main
```

---

## 6. Paper disarm / manual control

Paper auto-arms; the `DISARMED` flag is how you stop it.

```bash
touch /opt/orb-bot/state/DISARMED    # skip every session until undone
rm /opt/orb-bot/state/DISARMED       # resume
ls -la /opt/orb-bot/state/DISARMED 2>/dev/null && echo "DISARMED" || echo "will trade"

systemctl start orb-bot              # run a session off-schedule
systemctl stop orb-bot               # ⚠ DOES NOT FLATTEN POSITIONS — close by hand in Alpaca
```

---

## Notifications

ntfy topic `orb-bot-64daf7d341cb127c` (subscribe in the ntfy phone app).

```bash
sudo -u orb /opt/orb-bot/deploy/notify.sh "test" "hello"
```
