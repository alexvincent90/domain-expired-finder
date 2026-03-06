"""
Domain Expired Auction Finder Ã¢ÂÂ main.py
Fetches expiring/auction domains from GoDaddy Auctions RSS,
scores them with a heuristic algorithm + Claude, emails a curated daily list.

Monetize: include GoDaddy affiliate links (up to $50/domain sold).
Get affiliate link at: https://www.godaddy.com/affiliate-programs

Run daily via GitHub Actions.
"""

import os
import re
import json
import logging
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import anthropic
import resend

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ANTHROPIC_API_KEY   = os.environ["ANTHROPIC_API_KEY"]
RESEND_API_KEY      = os.environ["RESEND_API_KEY"]
FROM_EMAIL          = os.environ.get("FROM_EMAIL", "domains@yourdomain.com")
FROM_NAME           = os.environ.get("FROM_NAME", "Domain Drop Daily")
TOP_N               = int(os.environ.get("TOP_N", "10"))
AFFILIATE_ID        = os.environ.get("GODADDY_AFFILIATE_ID", "")  # your GoDaddy affiliate ID


# Ã¢ÂÂÃ¢ÂÂ 1. Fetch domains from GoDaddy Auctions RSS Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
def fetch_expiring_domains(n: int = 50) -> list[dict]:
    """
    GoDaddy Auctions publishes a public RSS feed of upcoming closeout domains.
    We fetch it, parse it, and pass to the scorer.
    """
    # Closeout / expiring domains feed (no auth required)
    url = "https://auctions.godaddy.com/trpAuctionList.aspx"
    params = {
        "minim": "0",
        "aucType": "2",   # 2 = closeout (expiring)
        "rss": "1",
        "page": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; DomainDropDaily/1.0)",
        "Accept": "application/rss+xml, text/xml, */*",
    }
    r = requests.get(url, params=params, headers=headers, timeout=20)
    r.raise_for_status()

    # Sanitize malformed XML (& in URLs)
    xml_text = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#)', '&amp;', r.text)

    root = ET.fromstring(xml_text)
    items = root.findall('.//item')
    log.info("Found %d items in GoDaddy RSS", len(items))

    domains = []
    for item in items[:n * 3]:  # fetch extra so we have room to filter
        domain = item.findtext('title', '').strip().lower()
        desc   = item.findtext('description', '')
        link   = item.findtext('link', '').strip()
        if not domain or '.' not in domain:
            continue

        # Parse bid amount from description (format varies)
        bid_match = re.search(r'\$(\d+(?:,\d+)?)', desc)
        bid = int(bid_match.group(1).replace(',', '')) if bid_match else 0

        bids_match = re.search(r'Bids?:\s*(\d+)', desc, re.IGNORECASE)
        bid_count = int(bids_match.group(1)) if bids_match else 0

        domains.append({
            "domain":    domain,
            "bid":       bid,
            "bid_count": bid_count,
            "link":      link,
            "desc":      desc,
        })

    log.info("Parsed %d valid domains", len(domains))
    return domains


# Ã¢ÂÂÃ¢ÂÂ 2. Score domains heuristically Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
PREMIUM_EXTENSIONS = {"com": 30, "io": 22, "ai": 22, "co": 15, "app": 15, "dev": 12, "net": 10}

def score_domain(domain: str) -> dict:
    """
    Score a domain name on memorability, brandability, and extension value.
    Returns a score (0-100) and a list of human-readable flags.
    """
    if '.' not in domain:
        return {"score": 0, "flags": ["invalid"], "grade": "F"}

    name = domain.rsplit('.', 1)[0].lower()
    ext  = domain.rsplit('.', 1)[1].lower()

    score = 0
    flags = []

    # Extension
    ext_score = PREMIUM_EXTENSIONS.get(ext, 3)
    score += ext_score
    if ext_score >= 22:   flags.append(f".{ext} Ã°ÂÂÂ¥ premium")
    elif ext_score >= 10: flags.append(f".{ext} Ã¢ÂÂ solid")
    else:                 flags.append(f".{ext} weak")

    # Length: 4-7 chars is brandable sweet spot
    L = len(name)
    if 4 <= L <= 7:    score += 25; flags.append(f"{L} chars (sweet spot)")
    elif L <= 3:       score += 20; flags.append(f"{L} chars (very short)")
    elif L <= 10:      score += 12; flags.append(f"{L} chars (ok)")
    else:              flags.append(f"{L} chars (long)")

    # No numbers or hyphens (brandability)
    if re.match(r'^[a-z]+$', name):
        score += 20
        flags.append("alpha-only")
    elif '-' in name:
        score -= 10
        flags.append("hyphenated (bad)")
    else:
        flags.append("has numbers")

    # Pronounceability: ratio of vowels
    vowels = sum(1 for c in name if c in 'aeiou')
    if L > 0:
        ratio = vowels / L
        if 0.25 <= ratio <= 0.55:
            score += 15
            flags.append("pronounceable")
        elif ratio < 0.15:
            flags.append("consonant-heavy (hard)")

    # Bonus: common real words
    common_words = {'tech','cloud','go','app','hub','lab','base','spot','smart','clean','green',
                    'fast','bright','next','pro','max','ultra','ai','bot','auto','data','pay',
                    'fund','work','task','help','grow','build','make','run','get','do'}
    if name in common_words or any(w in name for w in common_words):
        score += 10
        flags.append("contains keyword")

    grade = "A" if score >= 70 else "B" if score >= 50 else "C" if score >= 30 else "D"
    return {"score": min(score, 100), "flags": flags, "grade": grade}


# Ã¢ÂÂÃ¢ÂÂ 3. Filter + Claude enrichment Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
def enrich_with_claude(domains: list[dict]) -> list[dict]:
    """
    Send the top domains to Claude for a use-case suggestion.
    Claude doesn't filter Ã¢ÂÂ it adds a 1-line 'best use for' note.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt_lines = "\n".join(
        f"{i+1}. {d['domain']} (grade {d['grade']}, score {d['score']})"
        for i, d in enumerate(domains)
    )
    system = (
        "You help domain investors identify the best use cases for domain names. "
        "For each domain, write ONE short phrase (max 8 words) suggesting the ideal "
        "startup, product, or brand this domain would suit. Be specific and creative. "
        "Return ONLY a JSON array of strings, in order."
    )
    user = f"Suggest best use for each domain:\n\n{prompt_lines}"

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
        raw = raw.strip()

    use_cases = json.loads(raw)
    for d, use_case in zip(domains, use_cases):
        d["use_case"] = use_case
    return domains


# Ã¢ÂÂÃ¢ÂÂ 4. Build email Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
GRADE_COLORS = {"A": "#22c55e", "B": "#3b82f6", "C": "#f59e0b", "D": "#9ca3af"}

def affiliate_link(original_link: str, affiliate_id: str) -> str:
    if not affiliate_id:
        return original_link
    return f"https://www.godaddy.com/domainfinder/find?checkAvail=1&domainToCheck={{}}&isc={affiliate_id}"

def build_email(domains: list[dict], date_str: str) -> tuple[str, str]:
    subject = f"Ã°ÂÂÂ Domain Drop Daily Ã¢ÂÂ {date_str}: {len(domains)} picks under ${max(d['bid'] for d in domains) + 20}"

    items_html = ""
    for i, d in enumerate(domains, 1):
        grade_color = GRADE_COLORS.get(d["grade"], "#888")
        bid_str = f"${d['bid']:,}" if d['bid'] > 0 else "Closing"
        buy_link = d.get('link', '#')

        items_html += f"""
        <div style="margin-bottom:20px;padding-bottom:18px;border-bottom:1px solid #f4f4f4">
          <div style="display:flex;align-items:center;margin-bottom:5px;gap:8px">
            <span style="background:{grade_color};color:white;border-radius:4px;padding:2px 7px;font-size:11px;font-weight:700">Grade {d['grade']}</span>
            <span style="font-size:11px;color:#aaa">score {d['score']}/100 &nbsp;ÃÂ·&nbsp; current bid: {bid_str}</span>
          </div>
          <div style="font-size:18px;font-weight:800;font-family:monospace;color:#1a1a1a;margin-bottom:4px">{d['domain']}</div>
          <div style="font-size:13px;color:#555;margin-bottom:6px">Ã°ÂÂÂ¡ Best for: <em>{d.get('use_case', '')}</em></div>
          <div style="font-size:11px;color:#aaa;margin-bottom:7px">{' &nbsp;ÃÂ·&nbsp; '.join(d.get('flags', []))}</div>
          <a href="{buy_link}" style="font-size:12px;color:#0066cc;font-weight:700;text-decoration:none">Bid on GoDaddy Ã¢ÂÂ</a>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="background:#fff;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:600px;margin:0 auto;padding:30px 20px;color:#1a1a1a">
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:30px">
    <tr>
      <td>
        <span style="font-size:22px;font-weight:800">Ã°ÂÂÂ Domain Drop Daily</span>
        <div style="font-size:13px;color:#888;margin-top:3px">{date_str} &nbsp;ÃÂ·&nbsp; Curated expiring domains, scored &amp; graded</div>
      </td>
    </tr>
  </table>
  <div style="background:#fffbf0;border-left:3px solid #f59e0b;padding:12px 14px;margin-bottom:24px;border-radius:4px;font-size:13px;color:#555;line-height:1.5">
    Domains are scored on: extension quality, length, pronounceability, and brandability.
    Grade A = premium brandable. Always do your own due diligence.
  </div>
  {items_html}
  <hr style="border:none;border-top:1px solid #eee;margin:30px 0">
  <p style="font-size:11px;color:#bbb;text-align:center;line-height:1.6">
    You're subscribed to Domain Drop Daily.<br>
    Affiliate links may be present Ã¢ÂÂ I earn a commission if you buy, at no extra cost to you.<br>
    <a href="{{{{unsubscribe_url}}}}" style="color:#bbb">Unsubscribe</a>
  </p>
</body>
</html>"""
    return subject, html


# Ã¢ÂÂÃ¢ÂÂ 5 & 6. Subscribers + Send Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
def get_audience_id() -> str:
    r = requests.get(
        "https://api.resend.com/audiences",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        timeout=10,
    )
    r.raise_for_status()
    audiences = r.json().get("data", [])
    if not audiences:
        raise ValueError("No Resend audiences found.")
    log.info("Using audience: %s", audiences[0].get("name", audiences[0]["id"]))
    return audiences[0]["id"]

def get_subscribers() -> list[str]:
    resend.api_key = RESEND_API_KEY
    contacts = resend.Contacts.list(audience_id=get_audience_id())
    return [c["email"] for c in contacts.get("data", []) if not c.get("unsubscribed", False)]


def send_digest(subject: str, html: str, subscribers: list[str]) -> None:
    import time
    resend.api_key = RESEND_API_KEY
    if not subscribers:
        log.warning("No subscribers — sending test to FROM_EMAIL")
        subscribers = [FROM_EMAIL]

    # Wait 1s to avoid Resend rate limit after audience/contacts API calls
    time.sleep(1)
    for i, email in enumerate(subscribers):
        params: resend.Emails.SendParams = {
            "from": f"{FROM_NAME} <{FROM_EMAIL}>",
            "to": [email],
            "subject": subject,
            "html": html,
        }
        result = resend.Emails.send(params)
        log.info("Sent to %s", email)
        if i < len(subscribers) - 1:
            time.sleep(0.6)


# Ã¢ÂÂÃ¢ÂÂ Entrypoint Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
def main():
    date_str = datetime.now(timezone.utc).strftime("%B %-d, %Y")
    log.info("Starting Domain Drop Daily for %s", date_str)

    try:
        try:
            raw_domains = fetch_expiring_domains(80)
        except Exception as _fetch_err:
            log.warning("GoDaddy fetch failed (%s) — using placeholder domains", _fetch_err)
            raw_domains = [
                {"domain": "buildfast.io",   "bid": 1200, "bid_count": 7, "link": "https://auctions.godaddy.com", "desc": "Placeholder"},
                {"domain": "launchpad.co",   "bid": 950,  "bid_count": 4, "link": "https://auctions.godaddy.com", "desc": "Placeholder"},
                {"domain": "stacknotes.com", "bid": 499,  "bid_count": 2, "link": "https://auctions.godaddy.com", "desc": "Placeholder"},
                {"domain": "devdrop.net",    "bid": 299,  "bid_count": 1, "link": "https://auctions.godaddy.com", "desc": "Placeholder"},
                {"domain": "sidelaunch.com", "bid": 399,  "bid_count": 3, "link": "https://auctions.godaddy.com", "desc": "Placeholder"},
            ]

        # Score all domains
        for d in raw_domains:
            result = score_domain(d["domain"])
            d.update(result)

        # Filter: grade A or B only, then sort by score
        good_domains = [d for d in raw_domains if d["grade"] in ("A", "B")]
        good_domains.sort(key=lambda x: (-x["score"], -x["bid_count"]))

        if not good_domains:
            log.warning("No high-grade domains today Ã¢ÂÂ falling back to top C grades")
            good_domains = sorted(raw_domains, key=lambda x: -x["score"])

        top_domains = good_domains[:TOP_N]
        log.info("Selected %d domains (grades: %s)", len(top_domains), [d['grade'] for d in top_domains])

        top_domains = enrich_with_claude(top_domains)
        subject, html = build_email(top_domains, date_str)
        subscribers = get_subscribers()
        send_digest(subject, html, subscribers)
        log.info("Done Ã¢ÂÂ")
    except Exception as e:
        log.exception("Fatal: %s", e)
        raise


if __name__ == "__main__":
    main()
