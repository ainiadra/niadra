"""
The admin panel's end-to-end check (scripts/admin-check.sh): reads the admin's secret as JSON on
standard input, never prints it, and checks the panel over HTTPS like a browser would.

    python3 scripts/admin_check.py <domain> < secret.json
"""
import http.cookiejar, json, sys, urllib.error, urllib.request

domain = sys.argv[1]
secret = json.load(sys.stdin)
admin, site = f"https://admin.{domain}", f"https://{domain}"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
problems = []


def call(url, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("User-Agent", "niadra-admin-check")
    if data:
        req.add_header("Content-Type", "application/json")
        req.add_header("Origin", admin)
    try:
        with opener.open(req, timeout=20) as res:
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def expect(what, ok):
    print(f"    {'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        problems.append(what)


status, headers, body = call(f"{admin}/contatos")
expect("the panel loads on its host", status == 200 and b"Painel da Niadra" in body)
expect("the page is noindex and no-store", headers.get("X-Robots-Tag", "").startswith("noindex") and headers.get("Cache-Control") == "no-store")
status, _, _ = call(f"{site}/api/admin/contacts")
expect("the admin API answers 404 on the site host", status == 404)
status, headers, _ = call(f"{admin}/api/admin/contacts")
expect("the admin API answers 401 without a session, noindex and no-store", status == 401 and headers.get("Cache-Control") == "no-store" and headers.get("X-Robots-Tag", "").startswith("noindex"))
status, _, _ = call(f"{site}/admin")
expect("the site host does not serve the panel", status == 404)

status, headers, body = call(f"{admin}/api/admin/login", {"email": secret["email"], "password": secret["password"]})
cookie = headers.get("Set-Cookie", "")
expect("sign-in with the password from Secrets Manager", status == 200)
expect("the session cookie is HttpOnly, Secure, SameSite=Strict, 12 hours", all(p in cookie for p in ("HttpOnly", "Secure", "SameSite=Strict", "Max-Age=43200")))
del secret

status, _, body = call(f"{admin}/api/admin/contacts")
contacts = json.loads(body).get("contacts", []) if status == 200 else []
expect(f"Contatos: {len(contacts)} contact requests", status == 200 and len(contacts) > 0)
for rng in ("today", "7d", "30d"):
    status, _, body = call(f"{admin}/api/admin/access?range={rng}")
    report = json.loads(body) if status == 200 else {}
    totals = report.get("totals", {})
    crawlers = ", ".join(f"{c['name']} {c['hits']}" for c in report.get("crawlers", [])[:5])
    expect(f"Acessos {rng}: {totals} since {report.get('since')}; {crawlers or 'no crawler yet'}", status == 200 and sum(totals.values()) > 0)
status, _, _ = call(f"{admin}/api/admin/logout", {})
status2, _, _ = call(f"{admin}/api/admin/contacts")
expect("sign-out ends the session", status == 200 and status2 == 401)
sys.exit(1 if problems else 0)
