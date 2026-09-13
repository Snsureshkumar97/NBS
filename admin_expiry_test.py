"""Accounts: the admin role and account expiry, against a throwaway store.

Never touches the real user file - accounts._path is pointed at a temp file."""
import datetime as dt, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import accounts

tmp = os.path.join(tempfile.mkdtemp(), "users.json")
accounts._path = lambda: tmp
PW = "correct horse battery"
today = accounts.today_ist()
day = lambda n: (today + dt.timedelta(days=n)).isoformat()
fails = []
def check(name, cond, extra=""):
    print(f"  {'ok  ' if cond else 'FAIL'} {name}  {extra}")
    if not cond:
        fails.append(name)

print("1. CREATING WITH AN EXPIRY")
ok, m = accounts.create_user("live@example.com", PW, expires=day(30)); check("a future date is accepted", ok, m)
ok, m = accounts.create_user("past@example.com", PW, expires=day(-1)); check("a past date is refused at creation", not ok, m)
ok, m = accounts.create_user("bad@example.com", PW, expires="13/10/2026"); check("a non-date is refused", not ok, m)
ok, _ = accounts.create_user("plain@example.com", PW); check("no expiry is still allowed", ok)

print("2. A LIVE ACCOUNT SIGNS IN AND IS TOLD ITS RENEWAL DATE")
tok, err = accounts.authenticate("live@example.com", PW); check("signs in", bool(tok) and not err, err or "")
sm = accounts.account_summary("live@example.com")
check("30 days left", sm["days_left"] == 30, f"{sm['days_left']} / {sm['expires_label']}")
check("valid THROUGH the expiry day itself", not accounts._expired({"expires": today.isoformat()}))

print("3. EXPIRING IT ENDS ACCESS - INCLUDING THE SESSION ALREADY OPEN")
check("session works before", accounts.session_user(tok) == "live@example.com")
ok, m = accounts.set_expiry("live@example.com", day(-1)); check("expiry moved into the past", ok, m)
check("the already-open session stops working", accounts.session_user(tok) is None)
t2, err = accounts.authenticate("live@example.com", PW)
check("a new login is refused", t2 is None)
check("and says why, with the date", bool(err) and "ended on" in err, err or "")
_, errw = accounts.authenticate("live@example.com", "definitely-not-it")
check("a WRONG password still gets the vague message", errw == "Wrong email or password.", errw or "")

print("4. RENEWING RESTORES IT")
ok, m = accounts.set_expiry("live@example.com", day(90)); check("renewed", ok, m)
t3, err = accounts.authenticate("live@example.com", PW); check("signs in again", bool(t3) and not err, err or "")
ok, m = accounts.set_expiry("live@example.com", "")
check("cleared to no expiry", ok and accounts.account_summary("live@example.com")["expires"] is None, m)

print("5. THE ADMIN CANNOT BE LOCKED OUT")
accounts.create_user("boss@example.com", PW)
ok, _ = accounts.set_role("boss@example.com", "admin"); check("made admin", ok and accounts.is_admin("boss@example.com"))
check("an ordinary account is not admin", not accounts.is_admin("plain@example.com"))
ok, m = accounts.set_expiry("boss@example.com", day(5)); check("an admin cannot be given an expiry", not ok, m)
d = accounts._load(); d["users"]["boss@example.com"]["expires"] = day(-10); accounts._save(d)
check("even a stale expiry left in the file cannot lock the admin out",
      not accounts._expired(accounts._load()["users"]["boss@example.com"]))
tb, eb = accounts.authenticate("boss@example.com", PW); check("admin signs in regardless", bool(tb) and not eb, eb or "")

print("6. THE GENERIC UPDATER CANNOT BYPASS ANY OF THIS")
for field, val in (("role", "admin"), ("expires", day(-1))):
    ok, m = accounts.update_user("plain@example.com", {field: val})
    check(f"update_user refuses '{field}'", not ok, m)
check("so plain@ is still not an admin", not accounts.is_admin("plain@example.com"))

print("7. UNATTENDED FEEDS SKIP EXPIRED ACCOUNTS")
accounts.update_user("plain@example.com", {"always_on": True})
check("an always-on account starts while valid", "plain@example.com" in accounts.always_on_users())
accounts.set_expiry("plain@example.com", day(-1))
check("and is skipped once expired - no Zerodha calls on a lapsed account",
      "plain@example.com" not in accounts.always_on_users())

print("8. WHAT THE ADMIN TAB IS SENT")
accounts.update_user("live@example.com", {"kite_token": "SECRET-BROKER-TOKEN"})
view = accounts.admin_view()
blob = repr(view)
check("no password hash, no broker token", "SECRET-BROKER-TOKEN" not in blob and "password" not in blob
      and not any(k in r for r in view for k in ("kite_token",)))
check("the Zerodha link still shows as connected", {r["email"]: r["zerodha"] for r in view}["live@example.com"])
check("the three real accounts listed (two were refused), admin first", len(view) == 3 and view[0]["admin"], str(len(view)))
check("expired status reported", {r["email"]: r["status"] for r in view}["plain@example.com"] == "expired")

print("9. SIGN OUT EVERYWHERE")
t4, _ = accounts.authenticate("live@example.com", PW)
ok, m = accounts.revoke_sessions("live@example.com"); check("revoked", ok, m)
check("every session for that account is gone", accounts.session_user(t4) is None and accounts.session_user(t3) is None)
check("the account itself still exists and can sign in", bool(accounts.authenticate("live@example.com", PW)[0]))

print()
print("ADMIN + EXPIRY TEST PASSED" if not fails else f"ADMIN + EXPIRY TEST FAILED: {fails}")
sys.exit(1 if fails else 0)
