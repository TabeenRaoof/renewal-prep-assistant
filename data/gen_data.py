"""
gen_data.py
-----------
Generates a small, deliberately MESSY dataset of commercial Business Owners
Policy (BOP) clients for a regional insurance agency.

Why hand-authored instead of Faker?
  Faker gives you clean, uniform records. Real agency data is the opposite:
  half-filled CRM rows, premiums written three different ways, scanned PDFs
  nobody can search, and emails that quietly contradict the policy on file.
  The whole point of this tool is to survive that mess, so the test data has
  to contain it on purpose.

What it produces, under ./clients and ./ :
  clients/<CLIENT_ID>/dec.pdf        -> the BOP declaration page (most clients)
  clients/<CLIENT_ID>/email_NN.txt   -> raw inbound client emails (some clients)
  ams_export.csv                     -> one inconsistent CRM/AMS row per client

Deliberate traps baked in (so the AI actually has to work):
  - one scanned / image-only PDF  -> local text extraction fails, forces the
    native-PDF fallback through the model
  - one email that CONTRADICTS the dec page (client opened a 2nd location, but
    the property limit on file never changed) -> a real renewal signal
  - a client with NO dec page (only email + CRM)  -> partial extraction
  - clients with NO email (only dec page + CRM)   -> no priority signal to mine
  - premiums written as "$4,200", "4200", "about 4.2k"; dates in 5 formats;
    blank phones / blank values; typos and lowercase in the emails
"""

import os
import csv
import datetime as dt

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from PIL import Image, ImageDraw, ImageFont

# Where this script lives -> write everything relative to here so it runs
# the same no matter which directory you launch it from.
HERE = os.path.dirname(os.path.abspath(__file__))
CLIENTS_DIR = os.path.join(HERE, "clients")

# Expiration dates are generated as an offset from "today" so the renewal-stage
# flags (urgent / act-now / heads-up / future) are meaningful whenever you run
# this. The data is generated once and committed, so this is stable enough.
TODAY = dt.date.today()


# ----------------------------------------------------------------------------
# The roster. Each client is the "ground truth" PLUS the messy way it shows up
# across the three sources. Fields:
#   id, name, contact, biz             -> identity
#   carrier, policy_no                 -> policy header
#   exp_offset                         -> days from today until expiration
#   premium                            -> the real number (we'll garble how it's written)
#   prior_premium                      -> previous term, or None (drives trend)
#   prop_limit, gl_occ, gl_agg, ded    -> coverage numbers
#   endorsements                       -> list of free-text endorsements
#   has_dec                            -> render a declaration PDF?
#   scanned                            -> render that PDF as an image (un-searchable)?
#   emails                             -> list of (subject, body) raw client emails
#   crm_note                           -> the free-text CRM note (messy)
#   crm_quirks                         -> dict of CRM-cell messiness (blank fields, weird dates)
# ----------------------------------------------------------------------------

CLIENTS = [
    {
        "id": "CL-2001", "name": "Ray Delgado", "contact": "r.delgado@delgadohvac.net",
        "phone": "(952) 555-0177", "biz": "Delgado Heating & Air",
        "carrier": "The Hartford", "policy_no": "BOP-4471-22",
        "exp_offset": 28,  # URGENT
        "premium": 8450, "prior_premium": 7600,
        "prop_limit": 250000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Equipment Breakdown", "Hired & Non-Owned Auto"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("renewal coming up?",
             "hey - i think our policy is up soon? premium jumped a good bit last year "
             "and honestly i cant take another hike like that. what are my options. also "
             "we added two new service vans over the winter if that matters\nthanks\nRay"),
        ],
        "crm_note": "invoice dispute resolved. price sensitive!! added vehicles - check coverage",
        "crm_quirks": {"last_contact": "4/29/2024", "value": "8450", "status": "Active"},
    },
    {
        "id": "CL-2002", "name": "Marcy Holt", "contact": "marcyholt88@gmail.com",
        "phone": "", "biz": "Holt Family Daycare",
        "carrier": "Nationwide", "policy_no": "BP 88213004",
        "exp_offset": 41,  # URGENT
        "premium": 5200, "prior_premium": 5100,
        "prop_limit": 150000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 500,
        "endorsements": ["Abuse & Molestation", "Medical Payments $5,000"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("re: still waiting",
             "hi - circling back AGAIN on the renewal paperwork from last month. nobody has "
             "gotten back to me. this is the second time im asking. can someone please just "
             "tell me whats going on. getting frustrated tbh\n- Marcy"),
        ],
        "crm_note": "paperwork pending, follow up!! no phone on file",
        "crm_quirks": {"last_contact": "2024-04-02", "value": "", "status": "active"},
    },
    {
        "id": "CL-2003", "name": "Tom Becker", "contact": "tom.becker@beckerroofing.com",
        "phone": "612-555-0193", "biz": "Becker Roofing",
        "carrier": "Liberty Mutual", "policy_no": "BOP9920175",
        "exp_offset": 63,  # ACT NOW
        "premium": 14200, "prior_premium": 11800,
        "prop_limit": 300000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 2500,
        "endorsements": ["Contractors Installation", "Waiver of Subrogation"],
        "has_dec": True, "scanned": True,  # SCANNED / IMAGE-ONLY PDF -> forces fallback
        "emails": [],
        "crm_note": "referral from Sandra Liu. roofing - higher risk class. scanned policy only",
        "crm_quirks": {"last_contact": "Apr 10 2024", "value": "14,200", "status": "new"},
    },
    {
        "id": "CL-2004", "name": "J. Park", "contact": "jpark.realty@outlook.com",
        "phone": "", "biz": "Park Realty",
        "carrier": "Travelers", "policy_no": "680-7C-114455",
        "exp_offset": 77,  # ACT NOW
        "premium": 6500, "prior_premium": None,
        "prop_limit": 200000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Professional Liability (E&O) - separate policy"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("following up",
             "following up on renewal. looks fine overall. one thing - can we time the start "
             "for july instead of june, got kids graduation stuff and ill be out. lmk"),
        ],
        "crm_note": "wants july start. E&O on separate policy - dont forget at renewal",
        "crm_quirks": {"last_contact": "05/01/24", "value": "6500", "status": "negotiating"},
    },
    {
        "id": "CL-2005", "name": "Bev Nguyen", "contact": "bev.nguyen@gmail.com",
        "phone": "", "biz": "Bev's Boutique",
        "carrier": "Hanover", "policy_no": "ZBP 1188340",
        "exp_offset": 89,  # ACT NOW
        "premium": 3900, "prior_premium": 3950,
        "prop_limit": 120000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 500,
        "endorsements": ["Spoilage", "Sign Coverage"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("checking in :)",
             "hi hope youre doing well! no rush at all but whenever you get a sec, did the new "
             "policy documents ever come through? i looked and dont see them, might be in spam "
             "who knows lol. thanks so much! you guys are always so quick :)"),
        ],
        "crm_note": "policy docs not received - resend. happy client, values responsiveness",
        "crm_quirks": {"last_contact": "2024/04/22", "value": "3900", "status": "Active"},
    },
    {
        "id": "CL-2006", "name": "Carlos Morales", "contact": "c.morales.contracting@gmail.com",
        "phone": "", "biz": "Morales Contracting",
        "carrier": "CNA", "policy_no": "B 6610042",
        "exp_offset": 95,  # ACT NOW
        "premium": 18900, "prior_premium": 16200,
        "prop_limit": 400000, "gl_occ": 2000000, "gl_agg": 4000000, "ded": 5000,
        "endorsements": ["Additional Insured - Blanket", "Primary & Noncontributory"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("Fwd: docs",
             "see below, the bank wants proof of GL coverage for the new project. can you send "
             "the cert? they need additional insured listed. kind of time sensitive\n\n"
             "---------- Forwarded message ----------\nGC needs COI w/ AI endorsement by EOW"),
        ],
        "crm_note": "loan package. needs COI w additional insured for GC. growing - new project",
        "crm_quirks": {"last_contact": "2024-04-22", "value": "18900", "status": "active"},
    },
    {
        "id": "CL-2007", "name": "Frost", "contact": "orders@frostedpine.com",
        "phone": "651-555-0148", "biz": "Frosted Pine Bakery",
        "carrier": "Cincinnati", "policy_no": "EBP 0099217",
        "exp_offset": 108,  # ACT NOW
        "premium": 4600, "prior_premium": 4500,
        "prop_limit": 175000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Spoilage $25,000", "Food Contamination"],
        "has_dec": True, "scanned": False,
        # CONFLICT TRAP: email says a second location opened, but the dec page
        # (rendered below) still shows a single location and an unchanged limit.
        "emails": [
            ("quick update",
             "hey just a heads up we opened our second location over on Maple St back in march, "
             "its been going great. figured you should know. everything still covered right? "
             "the new spot has way more equipment than the first one"),
        ],
        "crm_note": "opened 2nd location Maple St ~march. VERIFY limits adequate!!",
        "crm_quirks": {"last_contact": "2024-04-30", "value": "4600", "status": "active"},
    },
    {
        "id": "CL-2008", "name": "Dwight S", "contact": "dwight.s@stellaroofing.com",
        "phone": "", "biz": "Stella Roofing",
        "carrier": "Erie", "policy_no": "Q40 8800113",
        "exp_offset": 118,  # ACT NOW
        "premium": 12750, "prior_premium": 12750,
        "prop_limit": 280000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 2500,
        "endorsements": ["Tools & Equipment", "Hired & Non-Owned Auto"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("cancel",
             "we need to cancel the review meeting thursday, something came up on a jobsite. "
             "can we do early next week instead? mon or tues morning works. not wed."),
        ],
        "crm_note": "reschedule renewal review. hard to reach during day - jobsites",
        "crm_quirks": {"last_contact": "", "value": "12750", "status": "active"},
    },
    {
        "id": "CL-2009", "name": "Priya Kapoor", "contact": "priya.kapoor@kapooraccounting.com",
        "phone": "", "biz": "Kapoor Accounting",
        "carrier": "Chubb", "policy_no": "3577-44-21",
        "exp_offset": 124,  # HEADS UP
        "premium": 5400, "prior_premium": 5200,
        "prop_limit": 160000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Cyber Liability $50,000", "Professional Liability"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("Re: Re: Re: renewal questionnaire",
             "ok i filled out the renewal form but it wouldnt let me submit, kept saying the "
             "EIN field was invalid? our EIN is 47-1839204. also you asked about prior claims - "
             "we have none. anything else you need from me?"),
        ],
        "crm_note": "EIN 47-1839204. form submit error. no prior claims. wants cyber reviewed",
        "crm_quirks": {"last_contact": "4-25-2024", "value": "5400", "status": "onboarding"},
    },
    {
        "id": "CL-2010", "name": "Mike", "contact": "mike@twincitiesflooring.com",
        "phone": "", "biz": "Twin Cities Flooring",
        "carrier": "Nationwide", "policy_no": "BP 77120955",
        "exp_offset": 133,  # HEADS UP
        "premium": 7800, "prior_premium": 7100,
        "prop_limit": 220000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Installation Floater", "Goods in Transit"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("budgeting",
             "planning my budget for next year - roughly what should i expect the policy to run? "
             "want to make sure im setting aside the right amount. paid the last invoice via zelle btw"),
        ],
        "crm_note": "paid via zelle. wants renewal estimate early for budgeting",
        "crm_quirks": {"last_contact": "2024-04-30", "value": "1800", "status": "active"},  # value typo'd low
    },
    {
        "id": "CL-2011", "name": "Joan Whitaker", "contact": "jwhitaker@whitakeragency.com",
        "phone": "", "biz": "Whitaker Agency",
        "carrier": "Travelers", "policy_no": "680-7C-902231",
        "exp_offset": 141,  # HEADS UP
        "premium": 9100, "prior_premium": 8400,
        "prop_limit": 240000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Professional Liability", "Cyber Liability $100,000"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("loss runs",
             "a carrier is asking for loss runs going back 5 years on our account. do you have "
             "that or do i need to pull it, honestly not sure where it lives anymore. third "
             "request this month its exhausting"),
        ],
        "crm_note": "loss run request. no claims last 5 yrs per file. detail oriented",
        "crm_quirks": {"last_contact": "", "value": "9100", "status": "active"},
    },
    {
        "id": "CL-2012", "name": "Greg", "contact": "greg@gregsautobody.biz",
        "phone": "", "biz": "Greg's Auto Body",
        "carrier": "Liberty Mutual", "policy_no": "BOP4471902",
        "exp_offset": 149,  # HEADS UP
        "premium": 11200, "prior_premium": 9800,
        "prop_limit": 260000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 2500,
        "endorsements": ["Garagekeepers $100,000", "Pollution Liability"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("RE: insurance stuff",
             "quick one - my old agent retired and you guys took over right? i need someone "
             "on top of this before it renews. last guy let it lapse once and it was a nightmare. "
             "dont judge the paperwork mess lol"),
        ],
        "crm_note": "prior agent retired. policy lapsed once before - DO NOT let lapse again",
        "crm_quirks": {"last_contact": "", "value": "11200", "status": "prospect"},
    },
    {
        "id": "CL-2013", "name": "Hank Olson", "contact": "holson@olsonandsons.com",
        "phone": "218-555-0102", "biz": "Olson & Sons Plumbing",
        "carrier": "CNA", "policy_no": "B 6610998",
        "exp_offset": 167,  # FUTURE
        "premium": 9600, "prior_premium": 9600,
        "prop_limit": 230000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Tools & Equipment", "Backup of Sewer & Drain"],
        "has_dec": True, "scanned": False,
        "emails": [],  # no email -> no priority signal to mine
        "crm_note": "no response in 5 months. confirm still in business before renewal",
        "crm_quirks": {"last_contact": "2023-11-08", "value": "9600", "status": "inactive"},
    },
    {
        "id": "CL-2014", "name": "Sandra Liu", "contact": "sliu@meridianlandscaping.co",
        "phone": "", "biz": "Meridian Landscaping",
        "carrier": "The Hartford", "policy_no": "BOP-7781-09",
        "exp_offset": 182,  # FUTURE
        "premium": 6700, "prior_premium": 6300,
        "prop_limit": 180000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Hired & Non-Owned Auto", "Herbicide/Pesticide Applicator"],
        "has_dec": True, "scanned": False,
        "emails": [],
        "crm_note": "seasonal business. great referral source - sends us clients",
        "crm_quirks": {"last_contact": "2024-03-15", "value": "6700", "status": "active"},
    },
    {
        "id": "CL-2015", "name": "", "contact": "newlead2024@yahoo.com",
        "phone": "", "biz": "Bright Smiles Dental",
        "carrier": "", "policy_no": "",
        "exp_offset": 205,  # FUTURE (but unknown - see below)
        "premium": 0, "prior_premium": None,
        "prop_limit": 0, "gl_occ": 0, "gl_agg": 0, "ded": 0,
        "endorsements": [],
        "has_dec": False, "scanned": False,  # NO DEC PAGE -> partial extraction only
        "emails": [
            ("hello",
             "saw your site. we run a dental practice, 2 locations. our current insurance is up "
             "for renewal in the fall sometime and we are not happy with the carrier. what would "
             "switching look like, roughly what does coverage like ours cost"),
        ],
        "crm_note": "dental, 2 locations. shopping - unhappy w current carrier. NO policy on file yet",
        "crm_quirks": {"last_contact": "", "value": "", "status": "lead"},
    },
    {
        "id": "CL-2016", "name": "Tina Brooks", "contact": "tina@brightpathprint.com",
        "phone": "651-555-0148", "biz": "BrightPath Print Shop",
        "carrier": "Cincinnati", "policy_no": "EBP 0044182",
        "exp_offset": 240,  # FUTURE
        "premium": 5300, "prior_premium": 5300,
        "prop_limit": 190000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Equipment Breakdown", "Electronic Data"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("URGENT need this today",
             "need a cert of insurance for a vendor by my 3pm today, i know its short notice. "
             "whatever you have on file is fine i just cant lose this contract. call my cell if easier"),
        ],
        "crm_note": "fast turnaround COI needs. reliable payer",
        "crm_quirks": {"last_contact": "Apr 10 2024", "value": "5300", "status": "active"},
    },
    {
        "id": "CL-2017", "name": "", "contact": "angryclient@protonmail.com",
        "phone": "", "biz": "Northside Auto Repair",
        "carrier": "Erie", "policy_no": "Q40 1102554",
        "exp_offset": 300,  # FUTURE
        "premium": 8800, "prior_premium": 8800,
        "prop_limit": 210000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 2500,
        "endorsements": ["Garagekeepers $75,000"],
        "has_dec": True, "scanned": False,
        "emails": [
            ("complaint",
             "this is ridiculous. i was billed twice for the policy. TWICE. i want it fixed and "
             "i want to know how it happened. if i dont hear back by friday im disputing with my "
             "bank and finding a new agent"),
        ],
        "crm_note": "DOUBLE CHARGE complaint - urgent service recovery. retention risk!!",
        "crm_quirks": {"last_contact": "2024-03-15", "value": "8800", "status": "churned?"},
    },
    {
        "id": "CL-2018", "name": "Dana Whitfield", "contact": "dana@ironforgefitness.com",
        "phone": "", "biz": "Iron Forge Fitness",
        "carrier": "Hanover", "policy_no": "ZBP 4471209",
        "exp_offset": 358,  # FUTURE
        "premium": 7200, "prior_premium": 6900,
        "prop_limit": 200000, "gl_occ": 1000000, "gl_agg": 2000000, "ded": 1000,
        "endorsements": ["Participant Liability", "Medical Payments $10,000"],
        "has_dec": True, "scanned": False,
        "emails": [],
        "crm_note": "gym - participant liability key. expanding hours, maybe adding classes",
        "crm_quirks": {"last_contact": "2024-04-25", "value": "7200", "status": "active"},
    },
]


# ----------------------------------------------------------------------------
# Helpers for messy rendering
# ----------------------------------------------------------------------------

def fmt_date(d, style):
    """Render a date in one of several real-world-inconsistent formats."""
    if style == "iso":
        return d.strftime("%Y-%m-%d")
    if style == "us":
        return d.strftime("%m/%d/%Y")
    if style == "us_short":
        return d.strftime("%-m/%-d/%y")
    if style == "long":
        return d.strftime("%B %d, %Y")
    if style == "dotted":
        return d.strftime("%m.%d.%Y")
    return d.isoformat()


def messy_premium(amount, style):
    """Write a dollar amount the inconsistent way a human would."""
    if style == "dollar_comma":
        return f"${amount:,}"
    if style == "plain":
        return str(amount)
    if style == "slash_yr":
        return f"{amount:,}/yr"
    if style == "approx_k":
        return f"about {round(amount/1000, 1)}k"
    return f"${amount:,.2f}"


# Each client gets a deterministic-but-varied formatting style so the mess is
# spread around rather than uniform.
DATE_STYLES = ["iso", "us", "us_short", "long", "dotted"]
PREMIUM_STYLES = ["dollar_comma", "plain", "slash_yr", "approx_k", "dollar_cents"]


# ----------------------------------------------------------------------------
# Declaration page rendering (text PDF)
# ----------------------------------------------------------------------------

def render_dec_pdf(client, path):
    """
    Draw a BOP declaration page. We intentionally vary label wording and number
    formatting between clients so the extractor can't rely on a fixed template.
    """
    eff = TODAY + dt.timedelta(days=client["exp_offset"] - 365)
    exp = TODAY + dt.timedelta(days=client["exp_offset"])

    # Pick per-client formatting flavors based on a stable index.
    idx = int(client["id"].split("-")[1])
    d_style = DATE_STYLES[idx % len(DATE_STYLES)]
    p_style = PREMIUM_STYLES[idx % len(PREMIUM_STYLES)]

    c = canvas.Canvas(path, pagesize=letter)
    w, h = letter
    y = h - 60

    def line(txt, dy=16, font="Helvetica", size=10):
        nonlocal y
        c.setFont(font, size)
        c.drawString(60, y, txt)
        y -= dy

    # Carrier letterhead-ish block (wording varies by carrier)
    line(client["carrier"].upper(), dy=22, font="Helvetica-Bold", size=14)
    line("COMMERCIAL LINES - BUSINESSOWNERS POLICY", dy=14, font="Helvetica-Bold", size=10)
    line("DECLARATIONS", dy=22, font="Helvetica-Bold", size=10)

    # Policy header. Label wording is deliberately not uniform.
    line(f"Policy Number:  {client['policy_no']}")
    line(f"Named Insured:  {client['biz']}")
    insured_contact = client["name"] if client["name"] else "(see account)"
    line(f"Insured Contact:  {insured_contact}")
    line(f"Policy Period:  {fmt_date(eff, d_style)}  to  {fmt_date(exp, d_style)}  12:01 AM")
    line("")

    # Coverage section
    line("COVERAGE SUMMARY", dy=18, font="Helvetica-Bold", size=10)
    line(f"   Building & Business Personal Property Limit ...... ${client['prop_limit']:,}")
    line(f"   General Liability - Each Occurrence .............. ${client['gl_occ']:,}")
    line(f"   General Liability - Aggregate .................... ${client['gl_agg']:,}")
    line(f"   Property Deductible .............................. ${client['ded']:,}")
    line("")

    # Endorsements (some clients have none rendered)
    if client["endorsements"]:
        line("FORMS & ENDORSEMENTS", dy=18, font="Helvetica-Bold", size=10)
        for e in client["endorsements"]:
            line(f"   - {e}")
        line("")

    # Premium - label and formatting vary
    prem_label = {0: "Total Policy Premium", 1: "Annual Premium",
                  2: "Premium", 3: "TOTAL PREMIUM DUE"}[idx % 4]
    line(f"{prem_label}:  {messy_premium(client['premium'], p_style)}",
         dy=16, font="Helvetica-Bold", size=10)

    # Some dec pages happen to show the prior term premium; most don't.
    if client["prior_premium"]:
        line(f"   (Prior Term Premium: ${client['prior_premium']:,})", size=9)

    line("")
    line(f"Producer:  Northstar Regional Insurance Group", size=9)
    line(f"Producer Code:  NRG-{idx:04d}", size=9)
    line("This declarations page is part of your policy. Retain for your records.", size=8)

    c.save()


def render_scanned_pdf(client, path):
    """
    Render the declaration as an IMAGE embedded in a PDF (no text layer).
    pdfplumber / pdftotext will get little or nothing from this, which forces
    the pipeline to fall back to sending the PDF to the model as a document.
    """
    eff = TODAY + dt.timedelta(days=client["exp_offset"] - 365)
    exp = TODAY + dt.timedelta(days=client["exp_offset"])

    img = Image.new("RGB", (1000, 1300), "white")
    draw = ImageDraw.Draw(img)
    try:
        font_b = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 26)
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except Exception:
        font_b = ImageFont.load_default()
        font = ImageFont.load_default()

    lines = [
        (client["carrier"].upper(), font_b),
        ("COMMERCIAL BUSINESSOWNERS POLICY - DECLARATIONS", font_b),
        ("", font),
        (f"Policy No: {client['policy_no']}", font),
        (f"Named Insured: {client['biz']}", font),
        (f"Policy Period: {fmt_date(eff, 'long')} to {fmt_date(exp, 'long')}", font),
        ("", font),
        (f"Building/BPP Limit: ${client['prop_limit']:,}", font),
        (f"GL Each Occurrence: ${client['gl_occ']:,}", font),
        (f"GL Aggregate: ${client['gl_agg']:,}", font),
        (f"Deductible: ${client['ded']:,}", font),
        ("", font),
        (f"Annual Premium: ${client['premium']:,}", font_b),
        ("", font),
        ("Producer: Northstar Regional Insurance Group", font),
        ("[ SCANNED COPY - fax quality ]", font),
    ]
    y = 60
    for txt, f in lines:
        draw.text((60, y), txt, fill="black", font=f)
        y += 40

    # Slight rotation + noise to make it look like a real scan (and harder).
    img = img.rotate(0.7, expand=False, fillcolor="white")
    img.save(path, "PDF", resolution=120.0)


# ----------------------------------------------------------------------------
# Email + CSV rendering
# ----------------------------------------------------------------------------

def render_emails(client, folder):
    for i, (subject, body) in enumerate(client["emails"], start=1):
        sender = client["name"] if client["name"] else client["contact"].split("@")[0]
        txt = (f"From: {sender} <{client['contact']}>\n"
               f"Subject: {subject}\n\n"
               f"{body}\n")
        with open(os.path.join(folder, f"email_{i:02d}.txt"), "w") as fh:
            fh.write(txt)


def write_csv(clients, path):
    """
    AMS/CRM export. A real insurance agency-management export carries policy
    fields (carrier, policy number, expiration, premium) alongside the contact
    row, so this one does too - that's what powers the portfolio dashboard.

    Intentionally inconsistent: mixed date formats, blank cells, one premium
    typo'd low (CL-2010, $1,800 vs the true $7,800 on the dec page -> a
    reconciliation catch), free-text notes.
    """
    header = ["client_id", "name", "company", "email", "phone", "status",
              "carrier", "policy_number", "effective_date", "expiration_date",
              "annual_premium", "last_contact", "notes"]

    # Spread the date mess around the rows.
    exp_styles = ["iso", "us", "us_short", "long", "dotted"]
    prem_styles = ["dollar_comma", "plain", "slash_yr", "dollar_cents"]

    with open(path, "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(header)
        for i, cl in enumerate(clients):
            q = cl["crm_quirks"]
            has_pol = cl["has_dec"] or bool(cl["carrier"])
            eff = TODAY + dt.timedelta(days=cl["exp_offset"] - 365)
            exp = TODAY + dt.timedelta(days=cl["exp_offset"])

            # CRM premium: usually the true number, but CL-2010 is typo'd low and
            # CL-2002 is blank (mirrors the messy original sample).
            if cl["id"] == "CL-2010":
                prem_val = "1800"
            elif not q.get("value"):
                prem_val = ""
            else:
                prem_val = messy_premium(cl["premium"], prem_styles[i % len(prem_styles)])

            wr.writerow([
                cl["id"],
                cl["name"],
                cl["biz"],
                cl["contact"],
                cl["phone"],
                q.get("status", ""),
                cl["carrier"],
                cl["policy_no"],
                fmt_date(eff, exp_styles[i % len(exp_styles)]) if has_pol and cl["has_dec"] else "",
                fmt_date(exp, exp_styles[i % len(exp_styles)]) if has_pol and cl["has_dec"] else "",
                prem_val,
                q.get("last_contact", ""),
                cl["crm_note"],
            ])


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    os.makedirs(CLIENTS_DIR, exist_ok=True)
    n_dec = n_scan = n_email = 0

    for cl in CLIENTS:
        folder = os.path.join(CLIENTS_DIR, cl["id"])
        os.makedirs(folder, exist_ok=True)

        if cl["has_dec"]:
            dec_path = os.path.join(folder, "dec.pdf")
            if cl["scanned"]:
                render_scanned_pdf(cl, dec_path)
                n_scan += 1
            else:
                render_dec_pdf(cl, dec_path)
            n_dec += 1

        if cl["emails"]:
            render_emails(cl, folder)
            n_email += len(cl["emails"])

    write_csv(CLIENTS, os.path.join(HERE, "ams_export.csv"))

    print(f"Generated {len(CLIENTS)} clients")
    print(f"  declaration PDFs : {n_dec}  (of which scanned/image-only: {n_scan})")
    print(f"  client emails    : {n_email}")
    print(f"  AMS export       : ams_export.csv")
    print(f"Output dir         : {CLIENTS_DIR}")


if __name__ == "__main__":
    main()
    # Regenerate fixtures immediately so mock mode stays in sync with the roster.
    # Avoids the common mistake of updating gen_data.py and forgetting to re-run
    # gen_fixtures.py separately.
    import subprocess, sys
    fixtures_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "gen_fixtures.py")
    subprocess.run([sys.executable, fixtures_script], check=True)
